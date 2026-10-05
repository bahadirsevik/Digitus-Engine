# -*- coding: utf-8 -*-
"""Giris / cikis / kimlik uclari.

Bu router `api_router`'in ALTINA baglanmaz: giris ucunun kendisi giris
istemez. main.py icinde dogrudan /api/v1/auth altina monte edilir.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.core.login import CurrentUser, get_current_user, read_session_token
from app.core.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.sessions import (
    SessionBackendUnavailable,
    clear_failed_logins,
    create_session,
    is_login_blocked,
    register_failed_login,
    revoke_all_sessions,
    revoke_session,
)
from app.database.models import User
from app.dependencies import get_db

router = APIRouter()


# Kullanici bulunamadiginda da parola dogrulama maliyeti odenir; aksi halde
# yanit suresi "bu e-posta kayitli mi" sorusunu ifsa eder. Gecersiz bir
# argon2 hash'i degil, GERCEK bir hash olmali ki maliyet esit olsun.
_DUMMY_HASH = hash_password("zaman-sizdirmasini-onleyen-sabit-deger")


class LoginRequest(BaseModel):
    # EmailStr BILEREK kullanilmadi: ek bir paket (email-validator) gerektirir
    # ve bu is tek yeni bagimlilikla (argon2-cffi) sinirli tutuldu. Girise
    # katı e-posta dogrulamasi gerekmez — deger zaten yalniz veritabaninda
    # eslesirse ise yarar.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("Gecerli bir e-posta adresi girin.")
        return normalized


class UserOut(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    # True ise frontend zorunlu parola degistirme ekranini gosterir.
    must_change_password: bool = False


class AuthStatusOut(BaseModel):
    """Giris zorunlu mu? Frontend acilista bunu sorar."""

    login_required: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


def _client_ip(request: Request) -> Optional[str]:
    """
    Istemci IP'si. nginx X-Forwarded-For set ediyor; ilk deger gercek
    istemcidir. Header yoksa soket adresine duser.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=int(settings.SESSION_TTL_DAYS) * 24 * 60 * 60,
        httponly=True,  # JS okuyamaz — XSS ile token sizdirilamaz
        secure=settings.APP_ENV == "production",  # yalniz HTTPS uzerinde
        samesite="lax",  # CSRF'e karsi; ayni site navigasyonunda calisir
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        path="/",
    )


@router.get("/status", response_model=AuthStatusOut, tags=["Auth"])
def auth_status():
    """
    Giris zorunlu mu?

    Bu uc KIMLIK DOGRULAMASI ISTEMEZ — bilerek. Frontend acilista bunu sorar
    ve `login_required` false ise giris ekranini hic gostermez.

    Bu, LOGIN_ENABLED feature flag'inin UCTAN UCA calismasini saglar: flag
    kapatildiginda frontend'i yeniden kurmaya gerek kalmadan sistem giris
    oncesi haline doner. Iptal yolunun calismasi buna baglidir.
    """
    return AuthStatusOut(login_required=bool(settings.LOGIN_ENABLED))


@router.post("/login", response_model=UserOut, tags=["Auth"])
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    E-posta + parola ile giris. Basarili olursa HttpOnly oturum cookie'si set edilir.

    Hatali e-posta ve hatali parola AYNI yaniti dondurur (kullanici ifsasini
    onlemek icin).
    """
    email = payload.email.strip().lower()
    ip = _client_ip(request)

    try:
        if is_login_blocked(email, ip):
            logger.bind(email=email, ip=ip).warning("Giris denemesi sinirlandi")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "TOO_MANY_ATTEMPTS",
                    "message": (
                        f"Cok fazla basarisiz deneme. "
                        f"{settings.LOGIN_ATTEMPT_WINDOW_MINUTES} dakika sonra tekrar deneyin."
                    ),
                },
            )

        user = db.query(User).filter(User.email == email).first()

        # Kullanici yoksa da dogrulama maliyeti odenir (zaman sizdirmasi).
        password_hash = user.password_hash if user else _DUMMY_HASH
        password_ok = verify_password(payload.password, password_hash)

        account_usable = (
            user is not None and user.is_active and user.deleted_at is None
        )

        if not password_ok or not account_usable:
            count = register_failed_login(email, ip)
            logger.bind(email=email, ip=ip, attempt=count).warning("Basarisiz giris")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "INVALID_CREDENTIALS",
                    "message": "E-posta veya parola hatali.",
                },
            )

        # Argon2 parametreleri yukseltildiyse hash'i sessizce tasi.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)

        clear_failed_logins(email, ip)
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()

        token = create_session(
            user_id=user.id,
            email=user.email,
            ip=ip,
            user_agent=request.headers.get("User-Agent"),
        )
    except SessionBackendUnavailable:
        logger.error("Oturum deposuna ulasilamadi — giris reddedildi")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SESSION_BACKEND_UNAVAILABLE",
                "message": "Giris su an yapilamiyor, lutfen tekrar deneyin.",
            },
        )

    _set_session_cookie(response, token)
    logger.bind(
        user_id=user.id,
        email=user.email,
        ip=ip,
        must_change_password=bool(user.must_change_password),
    ).info("Giris yapildi")
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        must_change_password=bool(user.must_change_password),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Auth"])
def logout(request: Request, response: Response):
    """
    Cikis. Oturumu sunucu tarafinda siler ve cookie'yi temizler.

    Giris yapilmamis olsa bile 204 doner — cikis istegi her zaman basarilidir.
    """
    token = read_session_token(request)
    if token:
        try:
            revoke_session(token)
        except SessionBackendUnavailable:
            # Depo erisilemez olsa da cookie'yi temizle: istemci tarafinda
            # cikis gerceklesir, oturum zaten TTL ile duser.
            logger.warning("Cikis sirasinda oturum deposuna ulasilamadi")
    _clear_session_cookie(response)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut, tags=["Auth"])
def me(current: CurrentUser = Depends(get_current_user)):
    """Giris yapmis kullanicinin kimligi. Giris yoksa 401."""
    return UserOut(
        id=current.id,
        email=current.email,
        full_name=current.full_name,
        must_change_password=current.must_change_password,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT, tags=["Auth"])
def change_password(
    payload: ChangePasswordRequest,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Parola degistirir ve kullanicinin TUM oturumlarini kapatir.

    Tum oturumlarin kapatilmasi bilinctir: parola degistirmenin amaci
    genellikle baskasinin erisimini kesmektir.
    """
    user = db.query(User).filter(User.id == current.id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_CURRENT_PASSWORD",
                "message": "Mevcut parola hatali.",
            },
        )

    problem = validate_password_strength(payload.new_password)
    if problem:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "WEAK_PASSWORD", "message": problem},
        )

    # Yeni parola eskisiyle ayni olmasin — gecici parolayi "degistirmis"
    # sayip ayni degeri tekrar koymak zorunlulugu anlamsiz kilardi.
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PASSWORD_UNCHANGED",
                "message": "Yeni parola mevcut parolayla ayni olamaz.",
            },
        )

    user.password_hash = hash_password(payload.new_password)
    # Zorunluluk kalkar: artik kullanicinin kendi belirledigi parola var.
    user.must_change_password = False
    db.commit()

    try:
        revoke_all_sessions(user.id)
    except SessionBackendUnavailable:
        logger.warning("Parola degisti ama oturumlar kapatilamadi (Redis yok)")

    logger.bind(user_id=user.id).info("Parola degistirildi, oturumlar kapatildi")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

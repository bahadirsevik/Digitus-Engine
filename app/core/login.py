# -*- coding: utf-8 -*-
"""Giris kapisi — oturum cozme ve zorunlu kilma.

Feature flag: `LOGIN_ENABLED` false ise dependency NO-OP davranir ve sistem
giris oncesi haliyle calisir. Iptal yolu budur (tek env degiskeni).

DIKKAT — X-API-Key bu kapiyi ACMAZ:
`frontend/nginx.conf` her istege gecerli `X-API-Key` header'ini kendisi
ekliyor. Eger API key gecerli bir giris kaniti sayilsaydi internetten gelen
her istek dogrulanmis kabul edilir ve bu kapi hicbir ise yaramazdi. Bu yuzden
kimlik YALNIZ oturum cookie'sinden cozulur. API key ayri ve bagimsiz bir
katman olarak kalir (app/core/security.py).

Kapsam siniri: bu modul kullanicinin KIM oldugunu dogrular, NEYE
erisebilecegini DEGIL. Rol ve kiraci ayrimi yok; giris yapan her kullanici
tum workspace'leri gorur. Veri izolasyonu ayri bir istir
(bkz. optimice/kullanici-yapisi-plan.md).
"""
from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.sessions import SessionBackendUnavailable, get_session
from app.database.models import User
from app.dependencies import get_db


@dataclass(frozen=True)
class CurrentUser:
    """Istek sahibinin kimligi."""

    id: int
    email: str
    full_name: Optional[str]
    # True ise kullanici gecici parola kullaniyor ve parolasini
    # degistirmeden veri uclarina erisemez.
    must_change_password: bool = False


# Giris gerektigini istemciye bildiren standart yanit. Frontend 401'i
# gorunce login ekranina yonlendirir.
_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={"code": "NOT_AUTHENTICATED", "message": "Giris yapilmasi gerekiyor."},
)

# 403, 401 DEGIL: kimlik dogrulanmistir, eksik olan yetkidir. 401 donmek
# frontend'i giris ekranina atar ve kullanici sonsuz donguye girer.
_PASSWORD_CHANGE_REQUIRED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={
        "code": "PASSWORD_CHANGE_REQUIRED",
        "message": "Devam etmek icin parolanizi degistirmeniz gerekiyor.",
    },
)


def read_session_token(request: Request) -> Optional[str]:
    """Istekteki oturum cookie'sini okur."""
    return request.cookies.get(settings.SESSION_COOKIE_NAME)


def _session_user_id(request: Request) -> Optional[int]:
    """
    Cookie'deki oturumu oturum deposundan (Redis) cozer ve kullanici id'sini
    doner; cookie yok / oturum yok / gecersiz ise None.

    DB'ye DOKUNMAZ: bu sayede gecersiz oturumlar DB oturumu acmadan reddedilir.
    """
    token = read_session_token(request)
    if not token:
        return None

    try:
        payload = get_session(token)
    except SessionBackendUnavailable:
        # Fail-closed: oturum deposu yoksa kimse dogrulanmis sayilmaz.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SESSION_BACKEND_UNAVAILABLE",
                "message": "Oturum dogrulanamiyor, lutfen tekrar deneyin.",
            },
        )

    if not payload:
        return None

    user_id = payload.get("user_id")
    if not isinstance(user_id, int):
        return None
    return user_id


def _load_active_user(db: Session, user_id: int) -> Optional[CurrentUser]:
    """
    Kullaniciyi her istekte DB'den okuruz: hesap pasife alinirsa veya
    silinirse acik oturumlar ANINDA gecersizlesir. Birincil anahtar
    uzerinden indeksli tek SELECT.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active or user.deleted_at is not None:
        return None

    return CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        must_change_password=bool(user.must_change_password),
    )


def resolve_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[CurrentUser]:
    """
    Oturumu cozer. Giris yoksa None doner — HATA FIRLATMAZ.

    `get_current_user` (/auth/me, /auth/change-password) icin Depends tabanli
    yol. Router seviyesindeki kapi (`require_login`) bunu KULLANMAZ — bkz. orada.
    """
    user_id = _session_user_id(request)
    if user_id is None:
        return None
    return _load_active_user(db, user_id)


@contextmanager
def _gate_db_session(request: Request) -> Iterator[Session]:
    """
    Kapinin kendi DB oturumu. `Depends(get_db)` KULLANILAMAZ: FastAPI
    dependency'leri govdeden ONCE cozer ve LOGIN_ENABLED=false iken bile
    her istekte DB oturumu acardi.

    FastAPI test override'lari (`app.dependency_overrides[get_db]`) burada da
    gecerlidir; saglayici generator ise `finally` blogu her yolda calisir.
    """
    provider = request.app.dependency_overrides.get(get_db, get_db)
    produced = provider()
    if isinstance(produced, Generator):
        try:
            yield next(produced)
        finally:
            produced.close()
    else:
        yield produced


def require_login(request: Request) -> Optional[CurrentUser]:
    """
    Giris zorunlu kapisi. Router seviyesinde dependency olarak kullanilir.

    LOGIN_ENABLED false ise hicbir sey yapmaz: ne DB oturumu acilir ne oturum
    deposuna (Redis) gidilir — cookie gonderilse bile. Bu yuzden yalniz
    `Request`'e baglidir; `Depends(get_db)` zinciri bilerek yoktur.

    Acikken: cookie / oturum yoksa DB oturumu da ACILMAZ (401).
    """
    if not settings.LOGIN_ENABLED:
        return None

    user_id = _session_user_id(request)
    if user_id is None:
        raise _UNAUTHENTICATED

    with _gate_db_session(request) as db:
        current = _load_active_user(db, user_id)

    if current is None:
        raise _UNAUTHENTICATED
    if current.must_change_password:
        # Gecici parola: kimlik DOGRU ama erisim yok. Frontend'deki zorunlu
        # parola ekrani atlatilsa bile veri uclari kapali kalir.
        # /api/v1/auth/* bu kapiya BAGLI DEGILDIR, dolayisiyla kullanici
        # parolasini degistirebilir.
        raise _PASSWORD_CHANGE_REQUIRED
    return current


def get_current_user(
    current: Optional[CurrentUser] = Depends(resolve_current_user),
) -> CurrentUser:
    """
    Kullaniciyi DONDUREN kapi — /auth/me gibi kimlige ihtiyaci olan uclar.

    Feature flag'den BAGIMSIZ olarak giris ister: flag kapaliyken de
    "ben kimim" sorusunun dogru cevabi "giris yapilmamis"tir.
    """
    if current is None:
        raise _UNAUTHENTICATED
    return current

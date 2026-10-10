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
from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.core.sessions import SessionBackendUnavailable, get_session, revoke_session
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


@dataclass(frozen=True)
class _SessionIdentity:
    """Oturum deposundan cozulen, henuz DB'ye karsi DOGRULANMAMIS kimlik."""

    token: str
    user_id: int
    # Oturum yukunun tasidigi `users.session_version`. Alan YOKSA 0 sayilir
    # (alan eklenmeden once acilan eski oturumlar); gecersiz tipte ise -1
    # (hicbir kullanici surumuyle eslesmez).
    session_version: int


def _payload_session_version(payload: dict) -> int:
    version = payload.get("session_version", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        return -1
    return version


def _session_identity(request: Request) -> Optional[_SessionIdentity]:
    """
    Cookie'deki oturumu oturum deposundan (Redis) cozer; cookie yok / oturum
    yok / gecersiz ise None.

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
    return _SessionIdentity(
        token=token,
        user_id=user_id,
        session_version=_payload_session_version(payload),
    )


def _drop_stale_session(token: str) -> None:
    """
    Surumu eskimis oturumu depodan silmeyi dener — EN IYI CABA.

    Reddetme kararini DB'deki surum verir; silme yalniz depoyu temizler. Depo
    hatasi (Redis yok vb.) istegi 500'e CEVIRMEZ: oturum zaten reddedildi.
    """
    try:
        revoke_session(token)
    except Exception:  # noqa: BLE001 - en iyi caba, reddetme zaten gerceklesti
        logger.warning("Surumu eskimis oturum depodan silinemedi")


def _load_active_user(db: Session, identity: _SessionIdentity) -> Optional[CurrentUser]:
    """
    Kullaniciyi her istekte DB'den okuruz: hesap pasife alinirsa veya
    silinirse acik oturumlar ANINDA gecersizlesir. Birincil anahtar
    uzerinden indeksli tek SELECT.

    Oturumun surumu kullanicinin guncel `session_version` degerine esit
    olmalidir: parola degisimi sayaci artirir, eski oturumlar Redis'te
    kalsa (silme basarisiz olsa / TTL tazelenip dursa) bile burada reddedilir.
    """
    user = db.query(User).filter(User.id == identity.user_id).first()
    if user is None or not user.is_active or user.deleted_at is not None:
        return None

    if identity.session_version != int(user.session_version or 0):
        _drop_stale_session(identity.token)
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
    identity = _session_identity(request)
    if identity is None:
        return None
    return _load_active_user(db, identity)


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

    identity = _session_identity(request)
    if identity is None:
        raise _UNAUTHENTICATED

    with _gate_db_session(request) as db:
        current = _load_active_user(db, identity)

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

# -*- coding: utf-8 -*-
"""Oturum deposu — Redis.

Neden Redis, neden yeni tablo degil:
  - TTL yerlesik; suresi dolan oturum kendi kendine silinir, temizlik isi yok.
  - Redis zaten kurulu (Celery broker'i). Yeni altyapi gelmiyor.
  - Geri alinabilirlik: oturum semasi yok, dolayisiyla geri donus icin
    dusurulecek tablo da yok.

DIKKAT — fail-closed:
`app.core.cache.get_redis_client` FAIL-OPEN'dir (Redis yoksa None doner ve
cagiran cache'siz devam eder). Oturumda bu davranis KABUL EDILEMEZ: Redis
erisilemezse kullanici dogrulanamaz, dolayisiyla erisim KAPANIR. Bu modul
None istemciyi hata olarak ele alir.

Token ve Redis anahtari:
Uretilen duz token YALNIZ cookie'de yasar. Redis'te anahtar olarak token'in
SHA-256 hash'i kullanilir. Redis dump'i sizsa bile icindeki hash'lerden
kullanilabilir oturum token'i turetilemez.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings
from app.core.cache import get_redis_client

# Anahtar onekleri. Celery ayni Redis'i broker olarak kullaniyor; bu onekler
# onun anahtar alani ile cakismaz.
_SESSION_PREFIX = "digitus:sess:"
_USER_SESSIONS_PREFIX = "digitus:sess:user:"
_LOGIN_ATTEMPT_PREFIX = "digitus:login:fail:"

TOKEN_BYTES = 32  # 256 bit entropi


class SessionBackendUnavailable(RuntimeError):
    """Redis'e ulasilamadi. Cagiran 503 dondurmelidir (fail-closed)."""


def _client():
    client = get_redis_client(decode_responses=True)
    if client is None:
        raise SessionBackendUnavailable("Oturum deposuna (Redis) ulasilamiyor.")
    return client


def hash_token(token: str) -> str:
    """Duz token -> Redis anahtarinda kullanilan SHA-256 hex hash."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_key(token_hash: str) -> str:
    return f"{_SESSION_PREFIX}{token_hash}"


def _user_key(user_id: int) -> str:
    return f"{_USER_SESSIONS_PREFIX}{user_id}"


def _ttl_seconds() -> int:
    return int(settings.SESSION_TTL_DAYS) * 24 * 60 * 60


# ───────────────────────── oturum yasam dongusu ─────────────────────────


def create_session(
    *,
    user_id: int,
    email: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    session_version: int = 0,
) -> str:
    """
    Yeni oturum acar ve DUZ token dondurur (cookie'ye konacak deger).

    Duz token hicbir yerde saklanmaz; cagirandan sonra geri alinamaz.

    session_version: kullanicinin O ANKI `users.session_version` degeri. Kapi
    (app/core/login.py) her istekte bunu DB'dekiyle karsilastirir; parola
    degisince sayac artar ve bu oturum Redis'ten silinmese bile gecersiz olur.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    token_hash = hash_token(token)
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "user_id": user_id,
        "email": email,
        "session_version": int(session_version),
        "ip": ip,
        # User-Agent uzun olabilir; denetim icin 200 karakter yeterli.
        "user_agent": (user_agent or "")[:200],
        "created_at": now,
        "last_seen_at": now,
    }

    ttl = _ttl_seconds()
    client = _client()
    pipe = client.pipeline()
    pipe.setex(_session_key(token_hash), ttl, json.dumps(payload))
    # Kullanicinin tum oturumlarini tek seferde iptal edebilmek icin indeks.
    pipe.sadd(_user_key(user_id), token_hash)
    pipe.expire(_user_key(user_id), ttl)
    pipe.execute()

    return token


def get_session(token: str, *, refresh: bool = True) -> Optional[dict[str, Any]]:
    """
    Token'a karsilik gelen oturumu dondurur; yoksa/suresi dolmussa None.

    refresh=True ise TTL tazelenir (kayan pencere): kullanici aktif oldukca
    oturum dusmez, hareketsiz kalirsa SESSION_TTL_DAYS sonra duser.
    """
    if not token:
        return None

    token_hash = hash_token(token)
    client = _client()
    raw = client.get(_session_key(token_hash))
    if raw is None:
        return None

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        # Bozuk kayit: oturumu dusur, fail-closed.
        client.delete(_session_key(token_hash))
        return None

    if refresh:
        payload["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        ttl = _ttl_seconds()
        pipe = client.pipeline()
        pipe.setex(_session_key(token_hash), ttl, json.dumps(payload))
        user_id = payload.get("user_id")
        if isinstance(user_id, int):
            pipe.expire(_user_key(user_id), ttl)
        pipe.execute()

    return payload


def revoke_session(token: str) -> None:
    """Tek oturumu kapatir (cikis). Yok olan token sessizce gecer."""
    if not token:
        return
    token_hash = hash_token(token)
    client = _client()
    raw = client.get(_session_key(token_hash))
    user_id: Optional[int] = None
    if raw:
        try:
            user_id = json.loads(raw).get("user_id")
        except (ValueError, TypeError):
            user_id = None

    pipe = client.pipeline()
    pipe.delete(_session_key(token_hash))
    if isinstance(user_id, int):
        pipe.srem(_user_key(user_id), token_hash)
    pipe.execute()


def revoke_all_sessions(user_id: int) -> int:
    """
    Kullanicinin TUM oturumlarini kapatir. Kapatilan oturum sayisini doner.

    Parola degisimi ve hesap kapatmada cagrilir.
    """
    client = _client()
    token_hashes = client.smembers(_user_key(user_id)) or set()
    pipe = client.pipeline()
    for token_hash in token_hashes:
        pipe.delete(_session_key(token_hash))
    pipe.delete(_user_key(user_id))
    pipe.execute()
    return len(token_hashes)


# ───────────────────── giris denemesi sinirlama ─────────────────────
# Kaba kuvvet yavaslatma. Sayac e-posta + IP ikilisine baglidir: tek IP'den
# birden fazla hesabi denemek de, tek hesabi birden fazla IP'den denemek de
# ayri ayri sayilir.


def _attempt_key(email: str, ip: Optional[str]) -> str:
    ident = hashlib.sha256(f"{email.lower()}|{ip or '-'}".encode("utf-8")).hexdigest()
    return f"{_LOGIN_ATTEMPT_PREFIX}{ident}"


def register_failed_login(email: str, ip: Optional[str]) -> int:
    """Basarisiz denemeyi sayar ve guncel sayiyi doner."""
    client = _client()
    key = _attempt_key(email, ip)
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, int(settings.LOGIN_ATTEMPT_WINDOW_MINUTES) * 60)
    count, _ = pipe.execute()
    return int(count)


def clear_failed_logins(email: str, ip: Optional[str]) -> None:
    """Basarili giriste sayaci sifirlar."""
    _client().delete(_attempt_key(email, ip))


def is_login_blocked(email: str, ip: Optional[str]) -> bool:
    """Deneme siniri asilmis mi?"""
    raw = _client().get(_attempt_key(email, ip))
    if raw is None:
        return False
    try:
        return int(raw) >= int(settings.LOGIN_MAX_ATTEMPTS)
    except (ValueError, TypeError):
        return False

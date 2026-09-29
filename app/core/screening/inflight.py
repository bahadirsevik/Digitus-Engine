# -*- coding: utf-8 -*-
"""Dağıtık provider inflight limiti (plan §7.5) — ATOMİK ZSET + Lua.

Job içi `concurrency` TEK BAŞINA global limit değildir: iki worker aynı
anda 6'şar çağrı yaparsa sağlayıcıya 12 eşzamanlı istek gider.

Codex 9. tur #6: `SCAN → SET → SCAN` dağıtık semafor DEĞİLDİR (SCAN
eşzamanlı değişiklikleri kaçırır, iki acquire aynı anda geçebilir).
Burada tek bir ZSET + Lua betiği kullanılır:
  - süresi geçmiş lease'ler skor aralığıyla ATOMİK temizlenir
  - kapasite kontrolü ve ZADD AYNI betikte (yarış yok)
  - release YALNIZ kendi token'ını siler
  - uzun çağrılarda lease `renew()` ile uzatılır (heartbeat)

FAIL-CLOSED: Redis/limiter yoksa sınırsız çağrıya DÜŞÜLMEZ.
"""
from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Optional

# Codex 10. tur #8: TTL, sağlayıcı HTTP timeout'undan (120s) GÜVENLİ
# MARJLA uzun olmalı — aksi halde Redis kesintisinde lease çağrı sürerken
# düşer, slot erken açılır ve global inflight sınırı aşılır.
LEASE_TTL_SECONDS = 180
RENEW_INTERVAL_SECONDS = 30
ACQUIRE_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 0.25
KEY = "corpus_screening:inflight:leases"

# Kapasite kontrolü + kayıt TEK atomik adım
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1] - ARGV[2])
local count = redis.call('ZCARD', KEYS[1])
if count < tonumber(ARGV[3]) then
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
  redis.call('EXPIRE', KEYS[1], math.ceil(ARGV[2] * 3))
  return 1
end
return 0
"""
# Yalnız HÂLÂ sahip olduğumuz lease yenilenir (aksi halde hayalet slot)
_RENEW_LUA = """
if redis.call('ZSCORE', KEYS[1], ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])
  return 1
end
return 0
"""


def _log_lease_loss(token: str, limit: int) -> None:
    """Lease kaybı GÖRÜNÜR olmalı: global sınır fiilen aşılmış olabilir."""
    try:
        from loguru import logger

        logger.error(
            f"INFLIGHT LEASE KAYBI (token {token[:8]}..., limit {limit}) — "
            f"çağrı sürerken slot serbest kalmış olabilir; global eşzamanlı "
            f"istek sınırı bu pencerede aşılmış olabilir")
    except Exception:  # noqa: BLE001
        pass


class InflightUnavailable(RuntimeError):
    """Limiter kullanılamıyor — sınırsız çağrı yerine fail-closed."""


class InflightBusy(RuntimeError):
    """Slot zaman aşımına kadar boşalmadı."""


class InflightLeaseLost(RuntimeError):
    """Lease süresi doldu/başkası temizledi — çağrı güvenli sayılmaz."""


class RedisInflightLimiter:
    """ZSET tabanlı atomik lease semaforu."""

    def __init__(self, redis_client, *, limit: int,
                 ttl_seconds: int = LEASE_TTL_SECONDS, key: str = KEY):
        if redis_client is None:
            raise InflightUnavailable("redis istemcisi yok")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise InflightUnavailable(f"limit >= 1 olmalı: {limit!r}")
        self._redis = redis_client
        self.limit = limit
        self.lease_losses = 0
        self.ttl = int(ttl_seconds)
        self.key = key
        try:
            self._acquire_script = redis_client.register_script(_ACQUIRE_LUA)
            self._renew_script = redis_client.register_script(_RENEW_LUA)
        except Exception as exc:  # noqa: BLE001
            raise InflightUnavailable(
                f"redis Lua betiği kaydedilemedi: {exc}") from exc

    def try_acquire(self) -> Optional[str]:
        token = uuid.uuid4().hex
        try:
            granted = self._acquire_script(
                keys=[self.key],
                args=[time.time(), self.ttl, self.limit, token])
        except Exception as exc:  # noqa: BLE001
            raise InflightUnavailable(f"redis lease hatası: {exc}") from exc
        return token if int(granted or 0) == 1 else None

    def renew(self, token: str) -> bool:
        try:
            return int(self._renew_script(
                keys=[self.key], args=[time.time(), token]) or 0) == 1
        except Exception as exc:  # noqa: BLE001
            raise InflightUnavailable(f"redis renew hatası: {exc}") from exc

    def release(self, token: str) -> None:
        try:
            self._redis.zrem(self.key, token)
        except Exception:  # noqa: BLE001
            pass          # TTL temizler

    @contextmanager
    def slot(self, *, timeout_seconds: float = ACQUIRE_TIMEOUT_SECONDS,
             sleep_fn=time.sleep):
        deadline = time.monotonic() + float(timeout_seconds)
        token = None
        while token is None:
            token = self.try_acquire()
            if token is not None:
                break
            if time.monotonic() >= deadline:
                raise InflightBusy(
                    f"global inflight slotu {timeout_seconds}s içinde "
                    f"boşalmadı (limit {self.limit})")
            sleep_fn(POLL_INTERVAL_SECONDS)
        stop = threading.Event()

        lost = threading.Event()

        def _heartbeat():
            # Uzun süren çağrıda lease sona ererse BAŞKASI slot açar;
            # sahiplik sürdükçe yenilenir. Kayıp SESSİZ GEÇİLMEZ: bayrak
            # kaldırılır ve çağrı bittiğinde sayaca yansır.
            while not stop.wait(RENEW_INTERVAL_SECONDS):
                try:
                    if not self.renew(token):
                        lost.set()
                        return
                except InflightUnavailable:
                    lost.set()
                    return

        beat = threading.Thread(target=_heartbeat, daemon=True)
        beat.start()
        try:
            yield token
        finally:
            stop.set()
            if lost.is_set():
                self.lease_losses += 1
                _log_lease_loss(token, self.limit)
            self.release(token)


class NullInflightLimiter:
    """Limiter YOK; kullanımı fail-closed'dır.

    `allow_unlimited=True` YALNIZ test/tek-worker geliştirme içindir.
    """

    def __init__(self, *, allow_unlimited: bool = False):
        self.allow_unlimited = bool(allow_unlimited)

    @contextmanager
    def slot(self, **_kwargs):
        if not self.allow_unlimited:
            raise InflightUnavailable(
                "global inflight limiter yok — sınırsız provider çağrısı "
                "yerine fail-closed (plan §7.5)")
        yield "unlimited"


def build_limiter(redis_url: Optional[str], *, limit: int,
                  allow_unlimited: bool = False):
    """Redis'ten limiter kurar; kurulamıyorsa fail-closed sarmalayıcı."""
    if not redis_url:
        return NullInflightLimiter(allow_unlimited=allow_unlimited)
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(redis_url, socket_timeout=2)
        client.ping()
        return RedisInflightLimiter(client, limit=limit)
    except Exception as exc:  # noqa: BLE001
        if allow_unlimited:
            return NullInflightLimiter(allow_unlimited=True)
        raise InflightUnavailable(f"redis limiter kurulamadı: {exc}") from exc

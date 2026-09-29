"""Ortak Redis cache provider (plan E).

Router katmanındaki `_get_redis_client` kopyalarının tek kaynağı — core
modülleri router'dan import EDEMEZ (katman kuralı). Fail-open: Redis
kurulu/erişilebilir değilse None döner, çağıran cache'siz devam eder
(repo konvansiyonu: Redis hataları sessizce yutulur).
"""
from typing import Optional

from app.config import settings


def get_redis_client(*, decode_responses: bool = True) -> Optional[object]:
    """redis.Redis istemcisi veya None (fail-open)."""
    try:
        import redis

        return redis.from_url(settings.REDIS_URL, decode_responses=decode_responses)
    except Exception:
        return None


def get_redis_bytes_client() -> Optional[object]:
    """Embedding cache için: ham float32 byte'ları — decode YOK.

    decode_responses=True istemcisi byte payload'ı bozar (UTF-8 decode
    dener); embedding cache'i her zaman bu istemciyle konuşmalıdır.
    """
    return get_redis_client(decode_responses=False)

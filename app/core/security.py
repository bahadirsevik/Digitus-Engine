"""
API Key authentication.

Opt-in: `API_KEY` env set edilmediyse koruma devre disidir, dependency no-op gibi
davranir. Production'da zorunlu (bkz. app.config.Settings.check_production_security).

Karsilastirma `secrets.compare_digest` ile yapilir; timing-attack korumasi saglar.
"""
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.config import settings


API_KEY_HEADER_NAME = "X-API-Key"

# auto_error=False: header yoksa FastAPI 403 firlatmasin, kontrolu biz yapalim.
_api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def verify_api_key(api_key: Optional[str] = Depends(_api_key_header)) -> None:
    """
    API key header'ini dogrular.

    Davranis:
      - settings.API_KEY None ise: pass-through (feature flag kapali).
      - set ise: X-API-Key header beklenir ve constant-time karsilastirilir.
    """
    expected = settings.API_KEY
    if not expected:
        return  # Feature flag off

    if not api_key or not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": API_KEY_HEADER_NAME},
        )

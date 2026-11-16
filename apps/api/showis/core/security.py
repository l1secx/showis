"""API kimlik dogrulama (tek kullanici / paylasilan token).

Sozlesme (AppSec SEC-01, T-108):
* Token ``SHOWIS_API_TOKEN`` ortam degiskeninden okunur.
* Iki baslik kabul edilir: ``X-API-Token: <token>`` veya
  ``Authorization: Bearer <token>``.
* Karsilastirma ``hmac.compare_digest`` ile yapilir (zamanlama sizintisi yok).
* Token yok/yanlis -> ``401`` + ``WWW-Authenticate: Bearer``.
* ``SHOWIS_AUTH_DISABLED=1`` yalniz test/dev bypass'idir; prod'da set edilirse
  surec basina bir kez ``logger.critical`` yazilir.
* ``/health`` her zaman aciktir (container healthcheck buna bagli).

Sunucuda token tanimli degilse API **fail-closed** davranir: ``503`` doner,
istek islenmez (yanlis yapilandirmayla acik API kalmasin).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Final

from fastapi import HTTPException, Request, status

logger = logging.getLogger("showis.security")

API_TOKEN_ENV: Final = "SHOWIS_API_TOKEN"
AUTH_DISABLED_ENV: Final = "SHOWIS_AUTH_DISABLED"

#: 401 yanitinda istemciye "Bearer ile tekrar dene" sinyali
BEARER_CHALLENGE: Final = {"WWW-Authenticate": "Bearer"}

_auth_disabled_warned = False


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def auth_disabled() -> bool:
    """``SHOWIS_AUTH_DISABLED`` yalniz test/dev icin; prod'da kritik log uretir."""
    global _auth_disabled_warned
    if not _truthy(os.getenv(AUTH_DISABLED_ENV)):
        return False
    if not _auth_disabled_warned:
        logger.critical(
            "%s=1: API kimlik dogrulamasi KAPALI — yalniz test/dev icin, prod'da set etmeyin",
            AUTH_DISABLED_ENV,
        )
        _auth_disabled_warned = True
    return True


def configured_token() -> str:
    """Sunucuda tanimli API token'i (yoksa bos string)."""
    return (os.getenv(API_TOKEN_ENV) or "").strip()


def extract_token(request: Request) -> str | None:
    """Istege eklenmis token'i basliklardan cikarir."""
    header_token = request.headers.get("x-api-token")
    if header_token and header_token.strip():
        return header_token.strip()

    authorization = request.headers.get("authorization") or ""
    scheme, _, credentials = authorization.partition(" ")
    if scheme.strip().lower() == "bearer" and credentials.strip():
        return credentials.strip()
    return None


def token_matches(candidate: str | None, expected: str) -> bool:
    """Sabit zamanli token karsilastirmasi (bos degerler asla eslesmez)."""
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate, expected)


async def require_token(request: Request) -> None:
    """Tum ``/api/v1`` router'larina baglanan kimlik dogrulama dependency'si."""
    if auth_disabled():
        return

    expected = configured_token()
    if not expected:
        logger.error("%s tanimli degil; API istekleri reddediliyor (fail-closed)", API_TOKEN_ENV)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API token yapılandırılmamış",
        )

    if not token_matches(extract_token(request), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya eksik API token",
            headers=BEARER_CHALLENGE,
        )

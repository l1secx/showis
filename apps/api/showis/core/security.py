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

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from typing import Final

from fastapi import Depends, HTTPException, Request, status
from showis.db.models import ApiUser, Case, Finding, Scan
from showis.db.session import get_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.security")

API_TOKEN_ENV: Final = "SHOWIS_API_TOKEN"
AUTH_DISABLED_ENV: Final = "SHOWIS_AUTH_DISABLED"
USER_TOKEN_HEADER: Final = "x-user-token"
ROLE_ADMIN: Final = "admin"
ROLE_ANALYST: Final = "analyst"
ROLE_VIEWER: Final = "viewer"
VALID_ROLES: Final = {ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER}


@dataclass(frozen=True)
class Actor:
    username: str
    role: str
    via: str


def hash_token(token: str) -> str:
    """Kullanıcı token'ı DB'de yalnız SHA-256 hash olarak tutulur."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_user_token(request: Request) -> str | None:
    value = request.headers.get(USER_TOKEN_HEADER)
    return value.strip() if value and value.strip() else None

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


async def require_token(
    request: Request, db: AsyncSession = Depends(get_db)
) -> Actor:
    """Kimlik doğrula; kullanıcı token'ı varsa rolünü, yoksa legacy admin'i döndür."""
    if auth_disabled():
        actor = Actor("dev", ROLE_ADMIN, "disabled")
        request.state.actor = actor
        return actor

    user_token = extract_user_token(request)
    if user_token:
        user = (
            await db.execute(
                select(ApiUser).where(
                    ApiUser.token_hash == hash_token(user_token),
                    ApiUser.active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Geçersiz kullanıcı anahtarı",
                headers=BEARER_CHALLENGE,
            )
        actor = Actor(user.username, user.role, "user-token")
        request.state.actor = actor
        return actor

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
    # Caddy basic auth kullanıcısı X-Actor başlığıyla gelir. Yalnız açıkça
    # etkinleştirildiğinde güvenilir; rol haritası zorunludur.
    if _truthy(os.getenv("SHOWIS_TRUST_ACTOR_HEADER")):
        header_actor = (request.headers.get("x-actor") or "").strip()
        if header_actor:
            roles = _actor_roles()
            role = roles.get(header_actor)
            if role is None:
                raise HTTPException(
                    status_code=403,
                    detail="Edge kullanıcısı için rol tanımlı değil",
                )
            actor = Actor(header_actor, role, "edge-header")
            request.state.actor = actor
            return actor
    actor = Actor("api-token", ROLE_ADMIN, "shared-token")
    request.state.actor = actor
    return actor


def _actor_roles() -> dict[str, str]:
    """``SHOWIS_ACTOR_ROLES=user:admin,viewer:viewer`` ayrıştırır."""
    raw = os.getenv("SHOWIS_ACTOR_ROLES") or ""
    result: dict[str, str] = {}
    for item in raw.replace(";", ",").split(","):
        user, _, role = item.strip().partition(":")
        if user and role in VALID_ROLES:
            result[user.strip()] = role.strip()
    return result


def get_actor(request: Request) -> Actor:
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, Actor):
        # require_token dependency'si bu router'da çalışmamışsa güvenli taraf.
        raise HTTPException(status_code=401, detail="Kimlik doğrulanmadı")
    return actor


_CASE_PATH_RE = re.compile(r"/cases/([^/]+)")
_SCAN_PATH_RE = re.compile(r"/scans/([^/]+)")
_FINDING_PATH_RE = re.compile(r"/findings/([^/]+)")


async def _case_owner(db: AsyncSession, case_id: str) -> str | None:
    return (
        await db.execute(select(Case.owner).where(Case.id == case_id))
    ).scalar_one_or_none()


async def require_write_access(
    request: Request,
    actor: Actor = Depends(require_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Viewer yazamaz; analyst yalnız sahibi olduğu/boş vakayı yazar."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if actor.role == ROLE_ADMIN:
        return
    if actor.role == ROLE_VIEWER:
        raise HTTPException(status_code=403, detail="Görüntüleyici rolü yazma yapamaz")

    case_id = (request.path_params or {}).get("case_id")
    if not case_id:
        match = _CASE_PATH_RE.search(request.url.path)
        case_id = match.group(1) if match else None
    if not case_id:
        scan_id = (request.path_params or {}).get("scan_id")
        if not scan_id:
            match = _SCAN_PATH_RE.search(request.url.path)
            scan_id = match.group(1) if match else None
        if scan_id:
            case_id = (
                await db.execute(select(Scan.case_id).where(Scan.id == scan_id))
            ).scalar_one_or_none()
    if not case_id:
        finding_id = (request.path_params or {}).get("finding_id")
        if not finding_id:
            match = _FINDING_PATH_RE.search(request.url.path)
            finding_id = match.group(1) if match else None
        if finding_id:
            case_id = (
                await db.execute(select(Finding.case_id).where(Finding.id == finding_id))
            ).scalar_one_or_none()
    if case_id:
        owner = await _case_owner(db, case_id)
        if owner and owner != actor.username:
            raise HTTPException(status_code=403, detail="Vaka başka bir analiste ait")


async def require_admin_access(actor: Actor = Depends(require_token)) -> None:
    if actor.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Bu işlem için admin rolü gerekli")

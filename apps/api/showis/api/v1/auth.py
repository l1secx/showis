"""Kullanıcı API anahtarları ve rol uçları."""

from __future__ import annotations

import base64
import os
import re
import secrets
import time
from collections import deque

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from showis.core.security import (
    VALID_ROLES,
    Actor,
    hash_token,
    require_admin_access,
    require_token,
)
from showis.db.models import ApiUser
from showis.db.session import get_db
from showis.services.audit import record_audit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")

#: Caddy `forward_auth` için edge parola doğrulaması. Parolalar bcrypt hash
#: olarak `SHOWIS_CADDY_USERS`/`SHOWIS_CADDY_USER` üzerinden gelir.
_EDGE_FAILURES: dict[str, deque[float]] = {}
_EDGE_WINDOW_SECONDS = 300.0
_EDGE_MAX_FAILURES = 10
_EDGE_MAX_FAILURES_PER_IP = 30
#: Bilinmeyen kullanıcıda da bcrypt maliyeti ödemek için sabit dummy hash.
_DUMMY_HASH = b"$2b$12$G7m5dmsg9ZbagnOXaAm9D.UJ1/4wBdyzhW51SQVMuDLhgpi1TkWm."


def edge_users() -> dict[str, str]:
    raw = (os.getenv("SHOWIS_CADDY_USERS") or "").strip()
    if not raw:
        raw = (
            f"{(os.getenv('SHOWIS_CADDY_USER') or '').strip()}:"
            f"{(os.getenv('SHOWIS_CADDY_PASSWORD_HASH') or '').strip()}"
        )
    users: dict[str, str] = {}
    for item in raw.replace(";", "\n").splitlines():
        username, _, password_hash = item.strip().partition(":")
        if username and password_hash:
            users[username] = password_hash
    return users


def _prune_failures(bucket: deque[float]) -> None:
    now = time.monotonic()
    while bucket and now - bucket[0] > _EDGE_WINDOW_SECONDS:
        bucket.popleft()


def _edge_limited(key: str, limit: int = _EDGE_MAX_FAILURES) -> bool:
    bucket = _EDGE_FAILURES.get(key)
    if bucket is None:
        return False
    _prune_failures(bucket)
    return len(bucket) >= limit


def _record_failure(key: str) -> None:
    bucket = _EDGE_FAILURES.setdefault(key, deque())
    _prune_failures(bucket)
    bucket.append(time.monotonic())
    if len(_EDGE_FAILURES) > 5000:
        for stale in [k for k, v in _EDGE_FAILURES.items() if not v]:
            _EDGE_FAILURES.pop(stale, None)


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization") or ""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


@router.api_route("/edge", methods=["GET", "POST"])
async def edge_auth(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> Response:
    """Caddy `forward_auth` ucu: Basic kimliği doğrular, audit ve limit uygular."""
    credentials = _basic_credentials(request)
    username = (credentials[0] if credentials else "")[:128]
    password = (credentials[1] if credentials else "")[:256]
    client = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if not client:
        client = request.client.host if request.client else "unknown"
    user_key = f"user:{username}"
    ip_key = f"ip:{client}"
    if _edge_limited(user_key) or _edge_limited(ip_key, _EDGE_MAX_FAILURES_PER_IP):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Çok fazla başarısız giriş denemesi",
        )

    password_hash = edge_users().get(username)
    valid = False
    if password_hash:
        try:
            valid = bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
        except ValueError:
            valid = False
    else:
        # Kullanıcı yoksa da bcrypt maliyeti öde (kullanıcı enumerasyonunu zorlaştır).
        try:
            bcrypt.checkpw(password.encode("utf-8"), _DUMMY_HASH)
        except ValueError:
            pass

    if not valid:
        _record_failure(user_key)
        _record_failure(ip_key)
        request.state.auth_audited = True
        try:
            await record_audit(
                db,
                action="auth.failed",
                actor=username or "edge",
                target="edge-basic",
                details={
                    "reason": "edge_basic",
                    "path": request.headers.get("x-forwarded-uri") or request.url.path,
                },
                actor_ip=client or None,
                user_agent_header=request.headers.get("user-agent"),
            )
        except Exception:
            await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz edge kimliği",
            headers={"WWW-Authenticate": 'Basic realm="Showis", charset="UTF-8"'},
        )

    response.headers["X-Actor"] = username
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    role: str = Field(default="analyst")


class UserOut(BaseModel):
    username: str
    role: str
    active: bool
    created_at: str | None = None


@router.get("/me")
async def me(actor: Actor = Depends(require_token)) -> dict:
    return {"username": actor.username, "role": actor.role, "via": actor.via}


@router.get("/users", dependencies=[Depends(require_admin_access)])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(ApiUser).order_by(ApiUser.username))).scalars().all()
    return [
        {
            "username": row.username,
            "role": row.role,
            "active": row.active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_access)],
)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db)) -> dict:
    username = payload.username.strip().lower()
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=422, detail="Kullanıcı adı geçersiz")
    if payload.role not in VALID_ROLES:
        raise HTTPException(
            status_code=422, detail="Rol admin|analyst|viewer olmalı"
        )
    existing = (
        await db.execute(select(ApiUser).where(ApiUser.username == username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Kullanıcı zaten var")

    token = f"showis_{secrets.token_urlsafe(32)}"
    row = ApiUser(
        username=username,
        role=payload.role,
        token_hash=hash_token(token),
        active=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "username": row.username,
        "role": row.role,
        "token": token,
        "note": "Token yalnız bir kez gösterilir; güvenli yerde saklayın.",
    }


@router.delete(
    "/users/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin_access)],
)
async def deactivate_user(username: str, db: AsyncSession = Depends(get_db)) -> None:
    row = (
        await db.execute(select(ApiUser).where(ApiUser.username == username.lower()))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Kullanıcı bulunamadı")
    if row.username == "api-token":
        raise HTTPException(status_code=422, detail="Yerleşik admin devre dışı bırakılamaz")
    row.active = False
    await db.commit()

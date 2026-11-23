"""Kullanıcı API anahtarları ve rol uçları."""

from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


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

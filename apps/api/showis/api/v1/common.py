"""v1 router'larının ortak DB doğrulama yardımcıları."""

from __future__ import annotations

from fastapi import HTTPException
from showis.db.models import Case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def require_case(db: AsyncSession, case_id: str) -> Case:
    """Vakayı döndürür; yoksa 404 (tüm router'larda tek davranış)."""
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Vaka bulunamadı")
    return case

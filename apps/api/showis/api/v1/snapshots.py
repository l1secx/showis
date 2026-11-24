"""Kanıt arşivi uçları: okunan/alıntılanan sayfaların metin kopyaları."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from showis.db.models import Case, EvidenceSnapshot
from showis.db.session import get_db
from showis.services.evidence_archive import LIST_LIMIT, snapshot_to_dict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["evidence"])


async def _require_case(db: AsyncSession, case_id: str) -> None:
    found = (await db.execute(select(Case.id).where(Case.id == case_id))).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="Case not found")


@router.get("/{case_id}/evidence/snapshots")
async def list_snapshots(
    case_id: str,
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=LIST_LIMIT),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_case(db, case_id)
    stmt = select(EvidenceSnapshot).where(EvidenceSnapshot.case_id == case_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(EvidenceSnapshot.url.ilike(like) | EvidenceSnapshot.title.ilike(like))
    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(EvidenceSnapshot.fetched_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return {"items": [snapshot_to_dict(row) for row in rows], "total": int(total)}


@router.get("/{case_id}/evidence/snapshots/{snapshot_id}")
async def get_snapshot(
    case_id: str, snapshot_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.id == snapshot_id, EvidenceSnapshot.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    data = snapshot_to_dict(row, with_text=True)
    data["integrity_ok"] = (
        hashlib.sha256((row.text or "").encode("utf-8", "replace")).hexdigest() == row.text_sha256
    )
    return data

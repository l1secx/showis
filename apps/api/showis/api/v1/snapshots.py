"""Kanıt arşivi uçları: okunan/alıntılanan sayfaların metin kopyaları."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from showis.db.models import EvidenceSnapshot
from showis.db.session import get_db
from showis.services.evidence_archive import LIST_LIMIT, snapshot_to_dict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["evidence"])

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _escape_like(value: str) -> str:
    """`LIKE` jokerlerini kaçır (kullanıcı `%`/`_` ile tüm arşivi taramasın)."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


from .common import require_case as _require_case  # noqa: E402


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
        text = q.strip()
        if _CONTROL_RE.search(text):
            raise HTTPException(status_code=422, detail="Geçersiz girdi: kontrol karakteri")
        like = f"%{_escape_like(text)}%"
        stmt = stmt.where(
            EvidenceSnapshot.url.ilike(like, escape="\\")
            | EvidenceSnapshot.title.ilike(like, escape="\\")
        )
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
        raise HTTPException(status_code=404, detail="Anlık görüntü bulunamadı")
    data = snapshot_to_dict(row, with_text=True)
    data["integrity_ok"] = (
        hashlib.sha256((row.text or "").encode("utf-8", "replace")).hexdigest() == row.text_sha256
    )
    return data

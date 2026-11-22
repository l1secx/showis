"""Bulgu doğrulama/reddetme + kanıt görüntüleme uçları."""

import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from showis.db.models import Evidence, Finding
from showis.db.session import get_db
from showis.services.audit import record_request_audit
from showis.services.retention import _evidence_file_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["findings"])

#: Önizleme üretilecek kanıt türleri (ikili/görsel veri dışarı verilmez).
_PREVIEW_KINDS = frozenset({"text", "html", "json"})
_MAX_PREVIEW_BYTES = 4096
_MAX_PREVIEW_CHARS = 600
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _evidence_preview(storage_key: str | None, kind: str) -> str | None:
    """Kanıt dosyasından güvenli, kısa metin önizlemesi (yol kaçışı korumalı)."""
    if kind not in _PREVIEW_KINDS:
        return None
    path = _evidence_file_path(storage_key)
    if path is None or not path.is_file():
        return None
    try:
        raw = path.read_bytes()[:_MAX_PREVIEW_BYTES]
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    if kind == "html":
        text = _HTML_TAG_RE.sub(" ", text)
    text = " ".join(text.split())
    return text[:_MAX_PREVIEW_CHARS] or None


class FindingPatch(BaseModel):
    status: str  # candidate | confirmed | rejected


@router.patch("/findings/{finding_id}")
async def patch_finding(
    finding_id: str,
    payload: FindingPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if payload.status not in ("candidate", "confirmed", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Geçersiz durum: candidate|confirmed|rejected",
        )
    res = await db.execute(select(Finding).where(Finding.id == finding_id))
    finding = res.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    previous_status = finding.status
    finding.status = payload.status
    if payload.status in ("confirmed", "rejected"):
        finding.verified_at = datetime.now(timezone.utc)
    await db.commit()

    # SEC-13: kullanıcı kararı adli iz bırakır (değer değil, durum geçişi)
    await record_request_audit(
        db=db,
        request=request,
        action="finding.update",
        case_id=finding.case_id,
        target=f"Finding: {finding.id}",
        details={
            "from": previous_status,
            "to": finding.status,
            "platform": finding.platform,
            "verified": finding.verified_at is not None,
        },
    )

    return {
        "id": finding.id,
        "status": finding.status,
        "score": finding.score,
        "verified_at": finding.verified_at.isoformat() if finding.verified_at else None,
    }


@router.get("/findings/{finding_id}/evidence")
async def get_finding_evidence(finding_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Finding).where(Finding.id == finding_id))
    finding = res.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    evidence_ids = finding.evidence_ids or []
    if not evidence_ids:
        return {"finding_id": finding.id, "evidence": []}

    ev_res = await db.execute(select(Evidence).where(Evidence.id.in_(evidence_ids)))
    items = ev_res.scalars().all()
    # Kanit zorunlulugu: kanitsiz bulgu gosterilmez — bulgu zaten evidence_ids icerir
    return {
        "finding_id": finding.id,
        "evidence": [
            {
                "id": ev.id,
                "source_url": ev.source_url,
                "kind": ev.kind,
                "content_hash": ev.content_hash,
                # SEC-20: sunucu dosya yolu istemciye verilmez, yalnız dosya adı
                "storage_key": Path(ev.storage_key).name if ev.storage_key else None,
                "filename": Path(ev.storage_key).name if ev.storage_key else None,
                "http_status": ev.http_status,
                "user_agent": ev.user_agent,
                "fetched_at": ev.fetched_at.isoformat() if ev.fetched_at else None,
                "expires_at": ev.expires_at.isoformat() if ev.expires_at else None,
                "preview": _evidence_preview(ev.storage_key, ev.kind),
            }
            for ev in items
        ],
    }

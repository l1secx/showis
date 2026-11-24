"""Kanıt arşivi: okunan/alıntılanan herkese açık sayfaların metin kopyası.

Yalnızca zaten politika kapısından (SSRF/robots) geçip okunmuş sayfalar saklanır;
bu katman ağa çıkmaz. Metin sınırlıdır ve saklama süresi vaka ayarını izler.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from showis.db.models import Case, EvidenceSnapshot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_SNAPSHOT_TEXT = 20_000
LIST_LIMIT = 200


def _expiry(case: Case) -> datetime | None:
    created = case.created_at
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created + timedelta(days=int(case.retention_days or 30))


async def save_snapshot(
    db: AsyncSession,
    *,
    case: Case,
    url: str,
    title: str,
    text: str,
    sha256: str,
    http_status: int | None = 200,
    session_id: str | None = None,
    source: str = "research",
) -> EvidenceSnapshot:
    """Sayfayı arşivler; aynı (vaka, url, içerik özeti) için mevcut kaydı döner."""
    digest = (sha256 or hashlib.sha256(text.encode("utf-8", "replace")).hexdigest())[:64]
    existing = (
        await db.execute(
            select(EvidenceSnapshot).where(
                EvidenceSnapshot.case_id == case.id,
                EvidenceSnapshot.url == url,
                EvidenceSnapshot.sha256 == digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    clean = text or ""
    truncated = len(clean) > MAX_SNAPSHOT_TEXT
    clean = clean[:MAX_SNAPSHOT_TEXT]
    row = EvidenceSnapshot(
        case_id=case.id,
        session_id=session_id,
        url=url,
        title=(title or "")[:300],
        sha256=digest,
        text_sha256=hashlib.sha256(clean.encode("utf-8", "replace")).hexdigest(),
        text=clean,
        text_truncated=truncated,
        http_status=http_status,
        source=source,
        expires_at=_expiry(case),
    )
    db.add(row)
    await db.flush()
    return row


def snapshot_to_dict(row: EvidenceSnapshot, *, with_text: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "case_id": row.case_id,
        "session_id": row.session_id,
        "url": row.url,
        "title": row.title,
        "sha256": row.sha256,
        "text_sha256": row.text_sha256,
        "text_length": len(row.text or ""),
        "text_truncated": bool(row.text_truncated),
        "http_status": row.http_status,
        "source": row.source,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }
    if with_text:
        data["text"] = row.text or ""
    else:
        data["excerpt"] = (row.text or "")[:280]
    return data


async def snapshot_ids_for_urls(
    db: AsyncSession, case_id: str, urls: list[str]
) -> dict[str, str]:
    """URL → en son snapshot id (vaka kapsamlı)."""
    if not urls:
        return {}
    rows = (
        await db.execute(
            select(EvidenceSnapshot.url, EvidenceSnapshot.id)
            .where(EvidenceSnapshot.case_id == case_id, EvidenceSnapshot.url.in_(urls))
            .order_by(EvidenceSnapshot.fetched_at.asc())
        )
    ).all()
    return {url: sid for url, sid in rows}  # sonuncu kazanır

"""Rapor servisi — vaka verisinden PDF/JSON raporu üretir ve saklar."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from showis.db.models import (
    Case,
    Entity,
    Evidence,
    Finding,
    Report,
    Target,
)
from showis_engine.report import generate_json_report, generate_pdf_report
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.report_service")

REPORT_DIR = Path(os.getenv("SHOWIS_REPORT_DIR", "./data/reports"))


def _finding_public(f: Finding) -> dict[str, Any]:
    return {
        "id": f.id,
        "entity_id": f.entity_id,
        "platform": f.platform,
        "url": f.url,
        "label": f.label,
        "status": f.status,
        "score": f.score,
        "breakdown": f.breakdown or {},
        "explanation": f.explanation,
        "evidence_ids": f.evidence_ids or [],
        "verified_at": f.verified_at.isoformat() if f.verified_at else None,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


async def build_report_data(db: AsyncSession, case_id: str) -> dict[str, Any]:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one()
    target = (
        await db.execute(select(Target).where(Target.case_id == case_id))
    ).scalar_one_or_none()
    findings = (
        (await db.execute(select(Finding).where(Finding.case_id == case_id)))
        .scalars()
        .all()
    )
    evidence = (
        (await db.execute(select(Evidence).where(Evidence.case_id == case_id)))
        .scalars()
        .all()
    )
    entities = (
        (await db.execute(select(Entity).where(Entity.case_id == case_id)))
        .scalars()
        .all()
    )

    return {
        "case": {
            "id": case.id,
            "name": case.name,
            "purpose": case.purpose,
            "legal_basis": case.legal_basis,
            "notes": case.notes,
            "status": case.status,
            "retention_days": case.retention_days,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        },
        "target": {
            "first_name": target.first_name if target else "",
            "last_name": target.last_name if target else "",
            "hints": target.hints if target else {},
            "name_forms": target.name_forms if target else [],
        },
        "entities": [
            {
                "id": e.id,
                "type": e.type,
                "platform": e.platform,
                "label": e.label,
                "url": e.url,
                "attributes": e.attributes or {},
            }
            for e in entities
        ],
        "findings": [_finding_public(f) for f in findings],
        "evidence": [
            {
                "id": ev.id,
                "source_url": ev.source_url,
                "kind": ev.kind,
                "content_hash": ev.content_hash,
                "storage_key": ev.storage_key,
                "http_status": ev.http_status,
                "fetched_at": ev.fetched_at.isoformat() if ev.fetched_at else None,
                "expires_at": ev.expires_at.isoformat() if ev.expires_at else None,
            }
            for ev in evidence
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def create_report(
    db: AsyncSession, case_id: str, fmt: str
) -> tuple[Report, bytes]:
    if fmt not in ("pdf", "json"):
        raise ValueError("Desteklenmeyen rapor formatı: pdf|json")

    data = await build_report_data(db, case_id)
    if fmt == "pdf":
        content = generate_pdf_report(data)
        ext = "pdf"
    else:
        content = generate_json_report(data)
        ext = "json"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{case_id}_{uuid.uuid4().hex[:8]}.{ext}"
    path = REPORT_DIR / filename
    path.write_bytes(content)

    report = Report(
        id=str(uuid.uuid4()),
        case_id=case_id,
        format=fmt,
        storage_key=str(path),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report, content

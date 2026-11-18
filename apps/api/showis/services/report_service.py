"""Rapor servisi — vaka verisinden PDF/JSON raporu üretir ve saklar."""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from showis.db.models import (
    Case,
    Edge,
    Entity,
    Evidence,
    Finding,
    Report,
    Scan,
    Target,
)
from showis.services.targets import db_target_to_contract
from showis_contracts import Target as TargetContract
from showis_engine.report import (
    build_report_sections,
    generate_html_report,
    generate_json_report,
    generate_pdf_report,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.report_service")

REPORT_DIR = Path(os.getenv("SHOWIS_REPORT_DIR", "./data/reports"))

#: Dosya adı için güvenli belirteç (T-313): yalnız [A-Za-z0-9_-], en çok 64 karakter.
#: `case_id` istemciden gelen bir yol parametresidir; `..%2F` gibi girdiler
#: dosya adına ya da dizin dışına sızmasın.
_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_-]")


def safe_case_token(case_id: str) -> str:
    """Dosya adı bileşeni: tehlikeli karakterler atılır, boşsa 'case'."""
    token = _SAFE_TOKEN_RE.sub("", str(case_id or ""))[:64]
    return token or "case"


def _report_path(case_id: str, ext: str) -> Path:
    """Rapor dosyasının güvenli yolu: `REPORT_DIR/<case_token>/<hex8>.<ext>`.

    T112-R3: vaka bazlı alt klasör — `glob("*")` çakışması ve yetim dosya
    karışıklığı biter, vaka silmede klasör bütünüyle kaldırılabilir.
    Yol daima `REPORT_DIR` içinde kalır (path traversal engellenir).
    """
    base = REPORT_DIR.resolve()
    folder = (base / safe_case_token(case_id)).resolve()
    if not folder.is_relative_to(base):
        raise ValueError("Rapor yolu dizin dışına çıkıyor")
    path = (folder / f"{uuid.uuid4().hex[:8]}.{ext}").resolve()
    if not path.is_relative_to(folder):
        raise ValueError("Rapor yolu dizin dışına çıkıyor")
    return path


def report_dir_for_case(case_id: str) -> Path:
    """Vakanın rapor klasörü (silme/testler için)."""
    return REPORT_DIR.resolve() / safe_case_token(case_id)


#: `reports.storage_key` bozuk/eski olduğunda denenecek dosya adları (geriye uyum)
def resolve_report_path(storage_key: str | None, case_id: str | None = None) -> Path | None:
    """Kayıtlı `storage_key`'den okunabilir dosya yolunu çözer.

    Sıra: (1) `storage_key` (mutlak yol, yeni biçim), (2) vaka klasöründe aynı
    dosya adı, (3) eski düz yerleşim (`REPORT_DIR/<ad>`). Bulunamazsa `None`.
    Dizin dışına çıkan yollar reddedilir.
    """
    base = REPORT_DIR.resolve()
    candidates: list[Path] = []
    if storage_key:
        candidates.append(Path(str(storage_key)))
        name = Path(str(storage_key)).name
        if name:
            if case_id:
                candidates.append(base / safe_case_token(case_id) / name)
            candidates.append(base / name)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - bozuk yol
            continue
        if not resolved.is_relative_to(base) or not resolved.is_file():
            continue
        return resolved
    return None


def purge_case_reports(case_id: str) -> tuple[list[str], int]:
    """Vakanın rapor klasörünü (dosyalar + klasör) siler.

    `reports.storage_key` satırları DB cascade ile gider; diskte kalan dosyalar
    burada temizlenir. Returns: (silinemeyen_yollar, silinen_dosya_sayisi).
    """
    folder = report_dir_for_case(case_id)
    failed: list[str] = []
    removed = 0
    if not folder.is_dir():
        return failed, removed
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Rapor dosyası silinemedi (%s): %s", path, type(exc).__name__)
                failed.append(str(path))
    if not failed:
        try:
            folder.rmdir()
        except OSError as exc:  # boş değilse/izin yoksa klasör kalır
            logger.warning("Rapor klasörü silinemedi (%s): %s", folder, type(exc).__name__)
            failed.append(str(folder))
    return failed, removed


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
        # C2: CONFIRMED kararının bağımsız doğrulama gerekçesi. DB kolonu C2a'da
        # eklenene kadar modelde alan olmayabilir → `getattr` ile güvenli okunur.
        "confidence_basis": getattr(f, "confidence_basis", "") or "",
    }


async def build_report_data(db: AsyncSession, case_id: str) -> dict[str, Any]:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one()
    target = (
        await db.execute(select(Target).where(Target.case_id == case_id))
    ).scalar_one_or_none()
    target_contract = db_target_to_contract(target) if target else TargetContract()
    target_block = {
        "kind": target_contract.kind,
        "first_name": target_contract.first_name,
        "last_name": target_contract.last_name,
        "nickname": target_contract.nickname,
        "hints": target_contract.hints,
        "name_forms": target_contract.name_forms,
    }
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
    # Entity'de evidence_ids kolonu yok: kanit baglantisi bulgu uzerinden kurulur
    entity_evidence: dict[str, list[str]] = {}
    for finding in findings:
        entity_evidence.setdefault(finding.entity_id, []).extend(finding.evidence_ids or [])

    edges = (
        (await db.execute(select(Edge).where(Edge.case_id == case_id))).scalars().all()
    )
    scan = (
        (
            await db.execute(
                select(Scan)
                .where(Scan.case_id == case_id)
                .order_by(Scan.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    data: dict[str, Any] = {
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
        # R21: hedef bloğu tek adaptörden gelir; hiçbir alan `null` olmaz.
        "target": target_block,
        "entities": [
            {
                "id": e.id,
                "type": e.type,
                "platform": e.platform,
                "label": e.label,
                "url": e.url,
                "attributes": e.attributes or {},
                "evidence_ids": sorted(set(entity_evidence.get(e.id, []))),
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
        "edges": [
            {
                "id": e.id,
                "type": e.type,
                "source_entity_id": e.source_entity_id,
                "target_entity_id": e.target_entity_id,
                "confidence": e.confidence,
                "reason": e.reason,
            }
            for e in edges
        ],
        "scan": {
            "id": scan.id if scan else None,
            "mode": scan.mode if scan else None,
            "status": scan.status if scan else None,
            "started_at": (scan.started_at.isoformat() if scan and scan.started_at else None),
            "finished_at": (scan.finished_at.isoformat() if scan and scan.finished_at else None),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # T-313: karar-odakli bolumler (executive summary, platform dagilimi,
    # kimlik/capraz baglanti, kronoloji, kanit ozeti) — JSON'a eklenir,
    # mevcut alanlar korunur (geriye uyumlu).
    for key, value in build_report_sections(data).items():
        data.setdefault(key, value)
    return data


async def create_report(
    db: AsyncSession, case_id: str, fmt: str
) -> tuple[Report, bytes]:
    if fmt not in ("pdf", "json", "html"):
        raise ValueError("Desteklenmeyen rapor formatı: pdf|json|html")

    data = await build_report_data(db, case_id)
    if fmt == "pdf":
        content = generate_pdf_report(data)
    elif fmt == "html":
        content = generate_html_report(data)
    else:
        content = generate_json_report(data)
    ext = fmt

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _report_path(case_id, ext)
    # Mert/T-314: konteyner umask'ı 077; yine de klasör iznini açıkça daralt
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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

"""Retention servisi — saklama suresi dolan kanit/gozlem verilerini temizler (S4-1).

Kurallar (Bölüm 2.2):
- Ham kanıt: case.retention_days (varsayılan 30 gun) → evidence.expires_at
- Gözlemler: ayni retention penceresi disina cikanlar silinir
- Bulgular: 90 gun (ayri retention_findings_days; varsayilan davranis:
  case silinene kadar korunur, purge'da dokunulmaz)
- Audit log: 365 gun
- Vaka silme: DELETE /cases/{id} kaskad siler (DB cascade)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from showis.db.models import AuditLog, Case, Evidence, Observation, Scan
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.retention")

AUDIT_RETENTION_DAYS = 365


def _evidence_file_path(storage_key: str | None) -> Path | None:
    """Evidence storage_key'i disk yoluna cevirir (path traversal korumali)."""
    if not storage_key:
        return None
    # Boş env değeri cwd'ye çözülmesin: varsayılan depo dizinine düş.
    base = Path(os.getenv("SHOWIS_EVIDENCE_DIR") or "./data/evidence").resolve()
    candidate = (base / storage_key).resolve()
    if not str(candidate).startswith(str(base) + os.sep):
        return None
    return candidate


async def purge_expired_evidence(db: AsyncSession) -> int:
    """Suresi dolmus evidence kayitlarini + dosyalarini siler."""
    now = datetime.now(timezone.utc)
    expired = (
        (await db.execute(select(Evidence).where(Evidence.expires_at < now)))
        .scalars()
        .all()
    )
    for ev in expired:
        path = _evidence_file_path(ev.storage_key)
        if path and path.exists():
            try:
                path.unlink()
            except OSError as exc:
                logger.warning("Evidence dosyasi silinemedi (%s): %s", path, exc)
    if expired:
        await db.execute(
            delete(Evidence).where(Evidence.id.in_([e.id for e in expired]))
        )
    await db.commit()
    count = len(expired)
    if count:
        logger.info("Retention: %d expired evidence kaydi silindi.", count)
    return count


async def purge_old_observations(db: AsyncSession) -> int:
    """Retention penceresi disina cikan gozlemleri siler (scan -> case join)."""
    now = datetime.now(timezone.utc)
    # Her case icin cutoff = case.created_at + retention_days
    cases = (await db.execute(select(Case))).scalars().all()
    total = 0
    for case in cases:
        if case.status == "deleted" or case.deleted_at:
            continue
        cutoff = case.created_at + timedelta(days=case.retention_days)
        if cutoff > now:
            continue
        # Bu case'in scan'lerine ait eski gozlemler
        scan_ids = (
            await db.execute(select(Scan.id).where(Scan.case_id == case.id))
        ).scalars().all()
        if not scan_ids:
            continue
        res = await db.execute(
            delete(Observation).where(
                Observation.scan_id.in_(scan_ids),
                Observation.collected_at < cutoff,
            )
        )
        total += getattr(res, "rowcount", 0) or 0
    await db.commit()
    if total:
        logger.info("Retention: %d eski observation kaydi silindi.", total)
    return total


async def purge_old_audit_logs(db: AsyncSession) -> int:
    """365 gunden eski audit kayitlarini siler."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=AUDIT_RETENTION_DAYS)
    res = await db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    await db.commit()
    count = getattr(res, "rowcount", 0) or 0
    if count:
        logger.info("Retention: %d eski audit kaydi silindi.", count)
    return count


async def run_retention_purge(db: AsyncSession) -> dict[str, Any]:
    """Tum retention politikalarini tek seferde calistirir."""
    evidence_deleted = await purge_expired_evidence(db)
    observations_deleted = await purge_old_observations(db)
    audit_deleted = await purge_old_audit_logs(db)
    return {
        "evidence_deleted": evidence_deleted,
        "observations_deleted": observations_deleted,
        "audit_deleted": audit_deleted,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

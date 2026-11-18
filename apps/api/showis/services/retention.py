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

from showis.db.models import (
    AuditLog,
    Case,
    Entity,
    Evidence,
    Observation,
    Scan,
    Seed,
    Task,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.retention")

AUDIT_RETENTION_DAYS = 365


def _evidence_file_path(storage_key: str | None) -> Path | None:
    """Evidence storage_key'i disk yoluna cevirir (path traversal korumali)."""
    if not storage_key:
        return None
    base = Path(os.getenv("SHOWIS_EVIDENCE_DIR", "./data/evidence")).resolve()
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
    # Her case icin cutoff = case.created_at + retention_days
    total = 0
    for case in await _expired_cases(db):
        cutoff = _as_aware(case.created_at) + timedelta(days=case.retention_days)
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


#: Retention sonrasi temizlenen PII alanlari (kayit silinmez, alan maskelenir).
TASK_PII_KEYS = ("email",)
#: `attributes["email"]` (publicapi/base + gravatar) ve `public_email` (commit yazari).
ENTITY_PII_KEYS = ("email", "public_email")


def _as_aware(value: datetime | None) -> datetime:
    """Naive datetime'i UTC kabul eder (SQLite/dev ile Postgres farki).

    `case.created_at` SQLite'ta naive donebilir; `now` aware oldugu icin
    kiyas `TypeError` verirdi (Z5 N4).
    """
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _expired_cases(db: AsyncSession) -> list[Case]:
    """Saklama suresi dolmus (ve silinmemis) vakalar."""
    now = datetime.now(timezone.utc)
    cases = (await db.execute(select(Case))).scalars().all()
    expired: list[Case] = []
    for case in cases:
        if case.status == "deleted" or case.deleted_at:
            # NOT: soft-delete eklenirse bu vakalar da purge kapsamina alinmali
            # (aksi halde PII suresiz kalir — Z5 N5).
            continue
        if _as_aware(case.created_at) + timedelta(days=case.retention_days) > now:
            continue
        expired.append(case)
    return expired


async def purge_expired_pii(db: AsyncSession) -> int:
    """Saklama suresi dolan vakalarda **ham e-posta** alanlarini temizler.

    Z3 I2 / Z5 I2: ham e-posta **dort** yerde kaliciydi:
    `tasks.params["email"]` (GitHub commit aramasi),
    `entities.attributes["public_email"]` (commit yazari),
    `entities.attributes["email"]` (publicapi/gravatar) ve `seeds.email`
    (vaka girisi). Retention penceresi dolunca bu alanlar **silinir**;
    kayitlar ve diger alanlar (username, name, email_tag) korunur.

    Donus: degistirilen kayit sayisi (task + seed + entity).
    """
    redacted = 0
    for case in await _expired_cases(db):
        scan_ids = (
            await db.execute(select(Scan.id).where(Scan.case_id == case.id))
        ).scalars().all()
        if scan_ids:
            tasks = (
                (await db.execute(select(Task).where(Task.scan_id.in_(scan_ids))))
                .scalars()
                .all()
            )
            for task in tasks:
                params = dict(task.params or {})
                if any(key in params for key in TASK_PII_KEYS):
                    for key in TASK_PII_KEYS:
                        params.pop(key, None)
                    # JSON mutation tracking icin yeniden atanir.
                    task.params = params
                    redacted += 1

        # Seed: vaka girisi sirasinda verilen ham e-posta da temizlenir.
        seeds = (
            (await db.execute(select(Seed).where(Seed.case_id == case.id)))
            .scalars()
            .all()
        )
        for seed in seeds:
            if seed.email:
                seed.email = None
                redacted += 1

        entities = (
            (await db.execute(select(Entity).where(Entity.case_id == case.id)))
            .scalars()
            .all()
        )
        for entity in entities:
            attributes = dict(entity.attributes or {})
            if any(key in attributes for key in ENTITY_PII_KEYS):
                for key in ENTITY_PII_KEYS:
                    attributes.pop(key, None)
                entity.attributes = attributes
                redacted += 1

    await db.commit()
    if redacted:
        logger.info("Retention: %d kayittan ham e-posta alani temizlendi.", redacted)
    return redacted


async def run_retention_purge(db: AsyncSession) -> dict[str, Any]:
    """Tum retention politikalarini tek seferde calistirir."""
    evidence_deleted = await purge_expired_evidence(db)
    observations_deleted = await purge_old_observations(db)
    pii_redacted = await purge_expired_pii(db)
    audit_deleted = await purge_old_audit_logs(db)
    return {
        "evidence_deleted": evidence_deleted,
        "observations_deleted": observations_deleted,
        "pii_redacted": pii_redacted,
        "audit_deleted": audit_deleted,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }

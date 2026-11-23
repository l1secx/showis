"""Vaka izleme — zamanı gelen vakalar için yeniden tarama oluşturur."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from showis.db.models import Case, Scan
from showis.services.audit import record_audit
from showis_engine.scan_modes import budget_for_mode, norm_mode
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.monitor")

ACTIVE_SCAN_STATUSES = ("queued", "running")
MAX_DUE_PER_TICK = 20


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def create_due_monitor_scans(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_DUE_PER_TICK,
) -> list[str]:
    """İzleme aralığı dolan vakalar için queued Scan satırları üretir."""
    now = _as_utc(now or datetime.now(timezone.utc))
    cases = (
        await db.execute(
            select(Case)
            .where(
                Case.monitor_enabled.is_(True),
                Case.status == "active",
            )
            .limit(200)
        )
    ).scalars().all()

    created: list[str] = []
    for case in cases:
        if len(created) >= limit:
            break
        active = (
            await db.execute(
                select(Scan.id)
                .where(
                    Scan.case_id == case.id,
                    Scan.status.in_(ACTIVE_SCAN_STATUSES),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if active:
            continue
        last = case.monitor_last_scan_at
        if last is not None:
            last = _as_utc(last)
            interval = timedelta(hours=max(1, int(case.monitor_interval_hours or 24)))
            if last + interval > now:
                continue

        mode = norm_mode(case.monitor_mode or "O2")
        scan = Scan(
            case_id=case.id,
            status="queued",
            mode=mode,
            budget=budget_for_mode(mode),
            policy_snapshot={"tier": "B", "safe_mode": True, "monitor": True},
            stats={"monitor": True},
        )
        db.add(scan)
        case.monitor_last_scan_at = now
        try:
            await db.flush()
            await db.commit()
        except IntegrityError:
            # Aktif tarama yarışı (0003 partial unique index).
            await db.rollback()
            continue
        created.append(scan.id)
        await record_audit(
            db=db,
            action="monitor.scan",
            case_id=case.id,
            target=f"Scan: {scan.id}",
            details={
                "mode": mode,
                "interval_hours": case.monitor_interval_hours,
            },
        )
        await db.commit()
        logger.info("İzleme taraması oluşturuldu: case=%s scan=%s", case.id, scan.id)

    return created

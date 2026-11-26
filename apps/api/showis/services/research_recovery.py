"""Bayat araştırma oturumlarının kurtarılması (süreç yeniden başlaması).

Araştırma ajanı API sürecinde `BackgroundTasks` ile koşar; süreç çökerse
`queued`/`running` oturum `awaiting_user` gibi kısmi unique index'i meşgul eder
ve aynı vakada yeni araştırma başlatılamaz. Beat görevi bu oturumları
`interrupted` işaretler; kullanıcı mevcut `resume` ucuyla kaldığı yerden sürer.
Durum yazımı dışında hiçbir DB/ağ işlemi yapılmaz.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from showis.db.models import ResearchSession
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.research.recovery")

#: Canlılık damgası bu süreden eskiyse süreç ölmüş sayılır (sn).
DEFAULT_STALE_SECONDS = float(os.getenv("SHOWIS_RESEARCH_STALE_SECONDS", "300"))
ACTIVE_STATUSES = ("queued", "running")
INTERRUPTED_REASON = "süreç yeniden başladı; Devam et ile sürdürün"


async def recover_stale_research_sessions(
    db: AsyncSession,
    *,
    stale_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Bayat `queued|running` oturumları `interrupted` işaretler.

    `awaiting_user` bilinçli olarak dokunulmaz: kullanıcı yanıtı bekleniyordur.
    `heartbeat_at` yoksa `created_at` kullanılır (eski satırlar).
    """
    current = now or datetime.now(timezone.utc)
    threshold = DEFAULT_STALE_SECONDS if stale_seconds is None else stale_seconds
    cutoff = current - timedelta(seconds=max(0.0, float(threshold)))
    rows = list(
        (
            await db.execute(
                select(ResearchSession)
                .where(
                    ResearchSession.status.in_(ACTIVE_STATUSES),
                    func.coalesce(
                        ResearchSession.heartbeat_at, ResearchSession.created_at
                    )
                    < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.status = "interrupted"
        row.error = INTERRUPTED_REASON
        row.finished_at = None
        row.heartbeat_at = current
        row.stats = {
            **(row.stats or {}),
            "activity": "Araştırma durduruldu; Devam et ile sürdürülebilir",
            "recovery": "stale_process",
        }
    if rows:
        await db.commit()
        logger.warning(
            "Bayat araştırma oturumları interrupted işaretlendi: %s",
            [row.id for row in rows],
        )
    return {"interrupted": [row.id for row in rows]}

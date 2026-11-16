"""Celery worker uygulamasi — osint kuyrugu + periyodik retention purge + stale kurtarma.

Gorevler:
- showis.retention.purge      : saklama suresi dolan verileri temizler (beat, gunluk)
- showis.scan.run             : tarama islemini worker'da calistirir (USE_CELERY=1)
- showis.scan.recover         : yarim kalmis/kuyrukta asili taramalari kurtarir (beat, 2 dk)

Onemli (asyncpg + prefork):
    ``asyncio.run()`` her gorevde yeni bir event loop acar. Asyncpg baglantilari
    olusturulduklari loop'a bagli oldugu icin havuzlu engine gorevler arasinda
    paylasilamaz; her gorev ``showis.db.session.task_session`` (NullPool) kullanir.
    Bkz. ``showis/db/session.py:create_task_engine``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init
from showis.db.models import Scan
from showis.db.session import reset_inherited_engine, task_session
from showis.services.retention import run_retention_purge
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.worker")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Yalnizca osint kuyrugunu tuketen worker icin gecerli ayarlar.
SCAN_QUEUE = os.getenv("SHOWIS_SCAN_QUEUE", "osint")
STALE_SCAN_SECONDS = int(os.getenv("SHOWIS_STALE_SCAN_SECONDS", "1800"))
MAX_RECOVERY_ATTEMPTS = int(os.getenv("SHOWIS_SCAN_MAX_RECOVERY_ATTEMPTS", "2"))
SCAN_MAX_RETRIES = int(os.getenv("SHOWIS_SCAN_MAX_RETRIES", "2"))
SCAN_RETRY_BACKOFF_SECONDS = float(os.getenv("SHOWIS_SCAN_RETRY_BACKOFF", "30"))
RECOVERY_INTERVAL_SECONDS = float(os.getenv("SHOWIS_SCAN_RECOVERY_INTERVAL", "120"))
BROKER_VISIBILITY_TIMEOUT = int(os.getenv("SHOWIS_BROKER_VISIBILITY_TIMEOUT", "3600"))

#: Bu durumlardaki tarama tekrar calistirilmaz (idempotency).
TERMINAL_SCAN_STATUSES = ("completed", "failed", "cancelled")

celery_app = Celery(
    "showis",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["showis.services.worker"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Worker yalnizca osint kuyrugunu tuketir; bu ayarlar o kuyrugu etkiler.
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": BROKER_VISIBILITY_TIMEOUT},
    task_routes={"showis.scan.*": {"queue": SCAN_QUEUE}},
    beat_schedule={
        "retention-purge-daily": {
            "task": "showis.retention.purge",
            "schedule": crontab(hour=3, minute=15),
        },
        "recover-stale-scans": {
            "task": "showis.scan.recover",
            "schedule": RECOVERY_INTERVAL_SECONDS,
        },
    },
)


@worker_process_init.connect
def _reset_worker_state_on_fork(**_kwargs: object) -> None:
    """Cocuk surecte ebeveynden miras kalan baglanti havuzunu bosaltir."""
    reset_inherited_engine()


def _enqueue_scan(scan_id: str) -> None:
    """Tarama gorevini osint kuyruguna birakir (broker hatasi cagirana aittir)."""
    celery_app.send_task("showis.scan.run", args=[scan_id], queue=SCAN_QUEUE)


async def _mark_scan_failed(scan_id: str, reason: str, *, error: str | None = None) -> bool:
    """Tarama hala terminal degilse ``failed`` olarak isaretler."""
    from showis.services.orchestrator import event_broadcaster

    async with task_session() as session:
        res = await session.execute(select(Scan).where(Scan.id == scan_id))
        scan = res.scalar_one_or_none()
        if scan is None:
            logger.warning("Isaretlenecek tarama bulunamadi: %s", scan_id)
            return False
        if scan.status in TERMINAL_SCAN_STATUSES:
            return False
        scan.status = "failed"
        scan.finished_at = datetime.now(timezone.utc)
        stats = {**(scan.stats or {}), "error": reason}
        if error:
            stats["error_detail"] = error
        scan.stats = stats
        await session.commit()

    await event_broadcaster.broadcast(scan_id, "scan.failed", {"status": "failed", "error": reason})
    return True


@celery_app.task(name="showis.retention.purge")
def retention_purge_task() -> dict:
    async def _run() -> dict:
        async with task_session() as session:
            return await run_retention_purge(session)

    return asyncio.run(_run())


@celery_app.task(
    name="showis.scan.run",
    bind=True,
    max_retries=SCAN_MAX_RETRIES,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_scan_task(self, scan_id: str) -> dict:
    """Tarama gorevini worker'da calistirir (API BackgroundTasks alternatifi).

    Idempotent: terminal durumdaki (completed/failed/cancelled) tarama tekrar
    calistirilmaz; redelivery/retry durumunda ikinci kez is uretmez.
    """
    from showis.services.connectors_factory import build_connectors, build_llm_client
    from showis.services.scanner import ScannerService

    async def _run() -> dict:
        async with task_session() as session:
            status = (
                await session.execute(select(Scan.status).where(Scan.id == scan_id))
            ).scalar_one_or_none()
            if status is None:
                logger.error("Tarama bulunamadi, gorev atlandi: %s", scan_id)
                return {"scan_id": scan_id, "status": "missing"}
            if status in TERMINAL_SCAN_STATUSES:
                logger.info("Tarama %s durumu '%s'; gorev atlandi (idempotency)", scan_id, status)
                return {"scan_id": scan_id, "status": "skipped", "scan_status": status}

            connectors = await build_connectors(session)
            llm_client = await build_llm_client(session)
            scanner = ScannerService(connectors=connectors, llm_client=llm_client)
            await scanner.execute_scan(scan_id, session)
        return {"scan_id": scan_id, "status": "done"}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        logger.exception("Scan gorevi basarisiz: %s", scan_id)
        retries = self.request.retries
        if retries < self.max_retries:
            countdown = min(SCAN_RETRY_BACKOFF_SECONDS * (2**retries), 300)
            logger.warning(
                "Scan %s yeniden denenecek (%s/%s, %ss sonra)",
                scan_id,
                retries + 1,
                self.max_retries,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        logger.error("Scan %s retry hakki tukendi; failed isaretleniyor", scan_id)
        asyncio.run(
            _mark_scan_failed(
                scan_id,
                "scan_task_failed",
                error=f"{type(exc).__name__}: {exc}"[:500],
            )
        )
        return {"scan_id": scan_id, "status": "failed", "error": type(exc).__name__}


def _as_utc(value: datetime) -> datetime:
    """SQLite naive datetime'lari UTC kabul eder."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _stale_reference(scan: Scan, stats: dict) -> datetime:
    """Taramanin "son canlilik" zamani: en yeni created_at/started_at/requeue.

    Yeniden kuyruga atildiginda ``stats.recovery_requeued_at`` yazilir; bu damga
    saati sifirlar, boylece ayni tarama ikinci bir recovery turunda yeniden
    kuyruga atilmaz (idempotency).
    """
    candidates = [scan.created_at]
    if scan.started_at is not None:
        candidates.append(scan.started_at)
    requeued_at = _parse_iso(stats.get("recovery_requeued_at"))
    if requeued_at is not None:
        candidates.append(requeued_at)
    return max(_as_utc(value) for value in candidates)


async def recover_stale_scans(
    session: AsyncSession,
    *,
    enqueue: Callable[[str], None] | None = None,
    now: datetime | None = None,
    stale_seconds: int | None = None,
    max_attempts: int | None = None,
) -> dict[str, list[str]]:
    """Esigi asan queued/running taramalari kurtarir.

    - Deneme sayaci ``scans.stats["recovery_attempts"]`` icinde tutulur (sema degisikligi yok).
    - ``max_attempts`` altinda: ``stats.recovery_requeued_at`` yazilir, durum ``queued``
      yapilir, ``started_at`` sifirlanir ve gorev yeniden kuyruga atilir.
    - ``max_attempts`` asilirsa: ``failed`` + ``stats.recovery="stale"`` + gerekce.
    - Idempotent: requeue damgasi saati sifirladigi icin ayni tarama tekrar kuyruga
      atilmaz; satir kilitleri (``FOR UPDATE SKIP LOCKED``) es zamanli turlari ayirir.
    """
    from showis.services.orchestrator import event_broadcaster

    now = now or datetime.now(timezone.utc)
    threshold = stale_seconds if stale_seconds is not None else STALE_SCAN_SECONDS
    limit = max_attempts if max_attempts is not None else MAX_RECOVERY_ATTEMPTS
    cutoff = now - timedelta(seconds=threshold)

    res = await session.execute(
        select(Scan).where(Scan.status.in_(("queued", "running"))).with_for_update(skip_locked=True)
    )
    candidates = res.scalars().all()

    requeued: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []

    for scan in candidates:
        stats = dict(scan.stats or {})
        if _stale_reference(scan, stats) > cutoff:
            skipped.append(scan.id)
            continue

        attempts = int(stats.get("recovery_attempts", 0) or 0)
        previous_status = scan.status
        if attempts >= limit:
            scan.status = "failed"
            scan.finished_at = now
            scan.stats = {
                **stats,
                "recovery": "stale",
                "reason": "stale_scan_max_attempts",
                "recovery_attempts": attempts,
                "recovered_at": now.isoformat(),
            }
            failed.append(scan.id)
            continue

        scan.status = "queued"
        scan.started_at = None
        scan.stats = {
            **stats,
            "recovery": "requeued",
            "reason": "stale_scan",
            "recovery_attempts": attempts + 1,
            "previous_status": previous_status,
            "recovery_requeued_at": now.isoformat(),
        }
        requeued.append(scan.id)

    await session.commit()

    for scan_id in requeued:
        try:
            (enqueue or _enqueue_scan)(scan_id)
        except Exception:
            logger.exception("Stale tarama yeniden kuyruga atilamadi: %s", scan_id)
            await _clear_requeue_marker(session, scan_id)

    for scan_id in requeued:
        await event_broadcaster.broadcast(scan_id, "scan.status", {"status": "queued"})
    for scan_id in failed:
        await event_broadcaster.broadcast(
            scan_id, "scan.failed", {"status": "failed", "error": "stale_scan_max_attempts"}
        )

    if requeued or failed:
        logger.warning(
            "Stale tarama kurtarma: requeued=%s failed=%s skipped=%s",
            requeued,
            failed,
            len(skipped),
        )
    return {"requeued": requeued, "failed": failed, "skipped": skipped}


async def _clear_requeue_marker(session: AsyncSession, scan_id: str) -> None:
    """Kuyruga atma basarisiz olduysa damgayi siler; sonraki tur tekrar dener."""
    res = await session.execute(select(Scan).where(Scan.id == scan_id))
    scan = res.scalar_one_or_none()
    if scan is None:
        return
    stats = dict(scan.stats or {})
    stats.pop("recovery_requeued_at", None)
    stats["recovery"] = "enqueue_failed"
    stats["recovery_attempts"] = max(int(stats.get("recovery_attempts", 1) or 1) - 1, 0)
    scan.stats = stats
    await session.commit()


@celery_app.task(name="showis.scan.recover", acks_late=True, reject_on_worker_lost=True)
def recover_scans_task() -> dict:
    """Beat: esigi asan queued/running taramalari kurtarir (varsayilan 2 dk'da bir)."""

    async def _run() -> dict:
        async with task_session() as session:
            return await recover_stale_scans(session, enqueue=_enqueue_scan)

    return asyncio.run(_run())

"""Stale scan kurtarma + worker gorev ayarlari testleri (T-107).

Kapsam:
- Esik (``SHOWIS_STALE_SCAN_SECONDS``) bazli stale tespiti
- Yeniden kuyruga atma (stats.recovery_requeued_at, started_at sifirlama)
- Deneme limitinde ``failed`` isaretleme
- Idempotency (ayni tarama iki kez kuyruga atilmaz)
- Celery gorevinin dogrudan cagrilmasi (broker'a gorev GONDERILMEZ)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from showis.db.base import Base
from showis.db.models import Case, Scan
from showis.services import worker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.fixture
async def db_url(tmp_path: Any) -> AsyncIterator[str]:
    """Dosya tabanli SQLite: farkli event loop'lar arasinda durum korunur."""
    url = f"sqlite+aiosqlite:///{tmp_path}/recovery.db"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield url


@asynccontextmanager
async def _session_ctx(url: str) -> AsyncIterator[AsyncSession]:
    """Uretimdeki ``task_session`` sozlesmesini taklit eder (engine per gorev)."""
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _add_scan(
    session: AsyncSession,
    *,
    status: str = "queued",
    age_seconds: int = 3600,
    stats: dict[str, Any] | None = None,
    started_offset: int | None = None,
    heartbeat_offset: int | None = None,
) -> Scan:
    now = datetime.now(timezone.utc)
    case = Case(name="Recovery Case", purpose="self_check", legal_basis="consent")
    session.add(case)
    await session.flush()
    scan = Scan(
        case_id=case.id,
        status=status,
        mode="O1",
        stats=stats or {},
        created_at=now - timedelta(seconds=age_seconds),
        started_at=None if started_offset is None else now - timedelta(seconds=started_offset),
        heartbeat_at=(
            None if heartbeat_offset is None else now - timedelta(seconds=heartbeat_offset)
        ),
    )
    session.add(scan)
    await session.commit()
    return scan


# --- (a) esik bazli stale tespiti -------------------------------------------


async def test_stale_detection_respects_threshold(db_url: str):
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        fresh = await _add_scan(session, status="queued", age_seconds=600)
        stale = await _add_scan(session, status="queued", age_seconds=3600)

        result = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )

    assert result["requeued"] == [stale.id]
    assert result["skipped"] == [fresh.id]
    assert result["failed"] == []
    assert enqueued == [stale.id]


async def test_running_scan_uses_started_at_as_reference(db_url: str):
    """started_at esigi asmissa running tarama da kurtarilir."""
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(session, status="running", age_seconds=7300, started_offset=7200)
        result = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )
        await session.refresh(scan)
        assert scan.status == "queued"
        assert scan.started_at is None

    assert result["requeued"] == [scan.id]
    assert enqueued == [scan.id]


async def test_live_running_scan_is_not_requeued(db_url: str):
    """Yeni baslamis (canli) tarama kurtarma tarafindan kesilmez."""
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(session, status="running", age_seconds=7300, started_offset=30)
        result = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )
        await session.refresh(scan)
        assert scan.status == "running"

    assert result["skipped"] == [scan.id]
    assert enqueued == []


async def test_old_started_but_fresh_heartbeat_not_requeued(db_url: str):
    """O3 duvar bütçesi eşiğe eşit olsa da canlılık damgası taze taramayı korur."""
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(
            session,
            status="running",
            age_seconds=7300,
            started_offset=1900,  # O3 max_wall_seconds (1800) aşılmış görünüyor
            heartbeat_offset=15,  # ama süreç 15 sn önce damga yazdı
        )
        result = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )
        await session.refresh(scan)
        assert scan.status == "running"

    assert result["skipped"] == [scan.id]
    assert enqueued == []


async def test_stale_heartbeat_requeues_running_scan(db_url: str):
    """Damga eskiyse (süreç ölmüş) running tarama yine kurtarılır."""
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(
            session,
            status="running",
            age_seconds=7300,
            started_offset=1900,
            heartbeat_offset=3600,
        )
        result = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )
        await session.refresh(scan)
        assert scan.status == "queued"
        assert scan.heartbeat_at is None

    assert result["requeued"] == [scan.id]


# --- (b) yeniden kuyruga atma ----------------------------------------------


async def test_requeue_writes_recovery_stats(db_url: str):
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(session, status="running", age_seconds=7200)
        await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800, max_attempts=2
        )
        await session.refresh(scan)

        assert scan.status == "queued"
        assert scan.started_at is None
        stats = dict(scan.stats or {})
        assert stats["recovery"] == "requeued"
        assert stats["reason"] == "stale_scan"
        assert stats["recovery_attempts"] == 1
        assert stats["previous_status"] == "running"
        assert isinstance(stats["recovery_requeued_at"], str)

    assert enqueued == [scan.id]


# --- (c) deneme limitinde failed -------------------------------------------


async def test_max_attempts_marks_scan_failed(db_url: str):
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(
            session,
            status="queued",
            age_seconds=7200,
            stats={"recovery_attempts": 2},
        )
        result = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800, max_attempts=2
        )
        await session.refresh(scan)

        assert scan.status == "failed"
        assert scan.finished_at is not None
        stats = dict(scan.stats or {})
        assert stats["recovery"] == "stale"
        assert stats["reason"] == "stale_scan_max_attempts"
        assert stats["recovery_attempts"] == 2

    assert result["failed"] == [scan.id]
    assert enqueued == []


# --- (d) idempotency -------------------------------------------------------


async def test_recovery_is_idempotent_across_runs(db_url: str):
    enqueued: list[str] = []
    async with _session_ctx(db_url) as session:
        scan = await _add_scan(session, status="queued", age_seconds=7200)
        first = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )

    # Ikinci tur: yeni gorev = yeni session (uretimde beat 2 dk sonra tekrar calisir)
    async with _session_ctx(db_url) as session:
        second = await worker.recover_stale_scans(
            session, enqueue=enqueued.append, stale_seconds=1800
        )

    assert first["requeued"] == [scan.id]
    assert second["requeued"] == []
    assert second["skipped"] == [scan.id]
    assert enqueued == [scan.id]


async def test_enqueue_failure_clears_requeue_marker(db_url: str):
    """Kuyruga atma basarisizsa damga silinir; sonraki tur tekrar dener."""

    def _boom(_scan_id: str) -> None:
        raise RuntimeError("broker down")

    async with _session_ctx(db_url) as session:
        scan = await _add_scan(session, status="queued", age_seconds=7200)
        await worker.recover_stale_scans(session, enqueue=_boom, stale_seconds=1800)
        await session.refresh(scan)

        stats = dict(scan.stats or {})
        assert stats["recovery"] == "enqueue_failed"
        assert "recovery_requeued_at" not in stats
        assert stats["recovery_attempts"] == 0


# --- Celery gorevi dogrudan cagrilir (broker'a gonderilmez) -----------------


async def test_recover_task_via_celery_entrypoint(db_url: str, monkeypatch: pytest.MonkeyPatch):
    enqueued: list[str] = []

    @asynccontextmanager
    async def _fake_task_session(*_args: object, **_kwargs: object) -> AsyncIterator[AsyncSession]:
        async with _session_ctx(db_url) as session:
            yield session

    monkeypatch.setattr(worker, "task_session", _fake_task_session)
    monkeypatch.setattr(worker, "_enqueue_scan", enqueued.append)

    async with _session_ctx(db_url) as session:
        stale = await _add_scan(session, status="queued", age_seconds=7200)
        fresh = await _add_scan(session, status="queued", age_seconds=30)

    # Celery gorev nesnesi dogrudan cagrilir; asyncio.run iceride calisir.
    result = await asyncio.to_thread(worker.recover_scans_task)

    assert result["requeued"] == [stale.id]
    assert result["skipped"] == [fresh.id]
    assert enqueued == [stale.id]


async def test_scan_task_skips_terminal_scan(db_url: str, monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def _fake_task_session(*_args: object, **_kwargs: object) -> AsyncIterator[AsyncSession]:
        async with _session_ctx(db_url) as session:
            yield session

    monkeypatch.setattr(worker, "task_session", _fake_task_session)

    async with _session_ctx(db_url) as session:
        done = await _add_scan(session, status="completed", age_seconds=60)

    result = await asyncio.to_thread(worker.run_scan_task, done.id)

    assert result == {"scan_id": done.id, "status": "skipped", "scan_status": "completed"}


async def test_mark_scan_failed_is_idempotent(db_url: str, monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def _fake_task_session(*_args: object, **_kwargs: object) -> AsyncIterator[AsyncSession]:
        async with _session_ctx(db_url) as session:
            yield session

    monkeypatch.setattr(worker, "task_session", _fake_task_session)

    async with _session_ctx(db_url) as session:
        queued = await _add_scan(session, status="queued", age_seconds=10)
        completed = await _add_scan(session, status="completed", age_seconds=10)

    assert await worker._mark_scan_failed(queued.id, "scan_task_failed")
    # Ikinci cagri ayni kaydi yeniden isaretlemez (terminal durum korunur)
    assert not await worker._mark_scan_failed(queued.id, "scan_task_failed")
    assert not await worker._mark_scan_failed(completed.id, "scan_task_failed")

    async with _session_ctx(db_url) as session:
        refreshed = await session.get(Scan, queued.id)
        assert refreshed is not None
        assert refreshed.status == "failed"
        assert (refreshed.stats or {})["error"] == "scan_task_failed"


# --- Celery/kuyruk ayarlari ------------------------------------------------


def test_worker_task_and_queue_configuration():
    assert worker.run_scan_task.max_retries == worker.SCAN_MAX_RETRIES == 2
    assert worker.run_scan_task.acks_late is True
    assert worker.run_scan_task.reject_on_worker_lost is True

    conf = worker.celery_app.conf
    assert conf.worker_prefetch_multiplier == 1
    assert conf.broker_transport_options["visibility_timeout"] == 3600
    assert conf.task_routes["showis.scan.*"]["queue"] == "osint"

    schedule = conf.beat_schedule
    assert schedule["recover-stale-scans"]["task"] == "showis.scan.recover"
    assert float(schedule["recover-stale-scans"]["schedule"]) == 120.0

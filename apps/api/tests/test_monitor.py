"""Vaka izleme — zamanı gelen otomatik tarama oluşturma."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.services.monitor import create_due_monitor_scans
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _case(factory, case_id: str, *, enabled=True, last=None) -> None:
    async with factory() as session:
        session.add(
            DbCase(
                id=case_id,
                name="İzleme",
                purpose="test",
                legal_basis="self_check",
                status="active",
                monitor_enabled=enabled,
                monitor_interval_hours=24,
                monitor_mode="O2",
                monitor_last_scan_at=last,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_zamani_gelen_vaka_icin_tarama_olusturur(factory):
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    await _case(factory, "case-monitor")
    async with factory() as session:
        ids = await create_due_monitor_scans(session, now=now)
    assert len(ids) == 1
    async with factory() as session:
        scan = (await session.execute(select(DbScan))).scalar_one()
        case = await session.get(DbCase, "case-monitor")
    assert scan.status == "queued"
    assert scan.mode == "O2"
    assert scan.stats["monitor"] is True
    assert case.monitor_last_scan_at is not None

    # Aynı an ikinci kez tarama üretilmez.
    async with factory() as session:
        assert await create_due_monitor_scans(session, now=now) == []


@pytest.mark.asyncio
async def test_aralik_dolmadan_tarama_olusturmaz(factory):
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    await _case(factory, "case-recent", last=now - timedelta(hours=2))
    async with factory() as session:
        assert await create_due_monitor_scans(session, now=now) == []


@pytest.mark.asyncio
async def test_kapali_izleme_tarama_uretmez(factory):
    await _case(factory, "case-off", enabled=False)
    async with factory() as session:
        assert await create_due_monitor_scans(session) == []

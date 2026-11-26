"""Restart kurtarması: bayat araştırma oturumları interrupted işaretlenir."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import ResearchSession
from showis.services.research_recovery import recover_stale_research_sessions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_only_stale_active_sessions_are_interrupted(db_factory):
    now = datetime.now(timezone.utc)
    async with db_factory() as db:
        for case_id in ("case-fresh", "case-running", "case-queued", "case-awaiting"):
            db.add(
                DbCase(
                    id=case_id,
                    name="Recovery",
                    purpose="test",
                    legal_basis="self_check",
                    retention_days=7,
                )
            )
        db.add_all(
            [
                ResearchSession(
                    id="fresh-queued",
                    case_id="case-fresh",
                    title="fresh",
                    question="soru",
                    status="queued",
                    created_at=now - timedelta(seconds=30),
                    heartbeat_at=now - timedelta(seconds=10),
                ),
                ResearchSession(
                    id="stale-running",
                    case_id="case-running",
                    title="stale running",
                    question="soru",
                    status="running",
                    created_at=now - timedelta(hours=2),
                    started_at=now - timedelta(hours=2),
                    heartbeat_at=now - timedelta(minutes=30),
                ),
                ResearchSession(
                    id="stale-queued-no-heartbeat",
                    case_id="case-queued",
                    title="stale queued",
                    question="soru",
                    status="queued",
                    created_at=now - timedelta(hours=2),
                    heartbeat_at=None,
                ),
                ResearchSession(
                    id="awaiting",
                    case_id="case-awaiting",
                    title="awaiting",
                    question="soru",
                    status="awaiting_user",
                    created_at=now - timedelta(hours=2),
                    heartbeat_at=now - timedelta(hours=1),
                ),
            ]
        )
        await db.commit()

    async with db_factory() as db:
        result = await recover_stale_research_sessions(db, stale_seconds=300, now=now)

    assert set(result["interrupted"]) == {"stale-running", "stale-queued-no-heartbeat"}

    async with db_factory() as db:
        rows = {
            row.id: row
            for row in (await db.execute(select(ResearchSession))).scalars().all()
        }
    assert rows["fresh-queued"].status == "queued"
    assert rows["awaiting"].status == "awaiting_user"
    assert rows["stale-running"].status == "interrupted"
    assert rows["stale-running"].error
    assert rows["stale-queued-no-heartbeat"].status == "interrupted"
    assert rows["stale-running"].stats.get("recovery") == "stale_process"

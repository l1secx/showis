"""Retention testleri — suresi dolan evidence/observation temizligi."""

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
for p in (
    ROOT / "apps/api",
    ROOT / "packages/contracts",
    ROOT / "packages/settings",
    ROOT / "packages/llm",
    ROOT / "packages/engine",
    ROOT / "packages/connectors",
):
    sys.path.insert(0, str(p))

os.environ.setdefault("MASTER_KEY", "test-master-key-for-pytest-only")

from showis.db.base import Base  # noqa: E402
from showis.db.models import Case, Evidence  # noqa: E402
from showis.services.retention import run_retention_purge  # noqa: E402
from sqlalchemy import select  # noqa: E402


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWIS_EVIDENCE_DIR", str(tmp_path / "evidence"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_expired_evidence_and_files(session, tmp_path):
    case = Case(id=str(uuid.uuid4()), name="c", purpose="p", legal_basis="self_check")
    session.add(case)
    await session.flush()

    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(parents=True)

    # Suresi dolmus evidence + diskte dosya
    old_key = "evidence/aa/bb.old.html"
    old_path = evidence_dir / old_key
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("eski kanit")

    ev_old = Evidence(
        id=str(uuid.uuid4()),
        case_id=case.id,
        source_url="https://example.com/old",
        kind="html",
        content_hash="oldhash",
        storage_key=old_key,
        fetched_at=datetime.now(timezone.utc) - timedelta(days=40),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    ev_new = Evidence(
        id=str(uuid.uuid4()),
        case_id=case.id,
        source_url="https://example.com/new",
        kind="html",
        content_hash="newhash",
        fetched_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    session.add_all([ev_old, ev_new])
    await session.commit()

    result = await run_retention_purge(session)
    assert result["evidence_deleted"] == 1
    assert not old_path.exists()

    remaining = (await session.execute(select(Evidence))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].id == ev_new.id

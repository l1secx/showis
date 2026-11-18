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


# ---------------------------------------------------------------------------
# Z4-4: retention sonrasi ham e-posta temizligi (tasks.params / entities.attributes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_expired_pii_eposta_alanlarini_temizler(session):
    from showis.db.models import Entity, Scan, Seed, Task

    expired = Case(
        id=str(uuid.uuid4()),
        name="expired",
        purpose="p",
        legal_basis="self_check",
        retention_days=1,
        created_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    fresh = Case(
        id=str(uuid.uuid4()),
        name="fresh",
        purpose="p",
        legal_basis="self_check",
        retention_days=30,
    )
    session.add_all([expired, fresh])
    await session.flush()

    old_scan = Scan(
        id=str(uuid.uuid4()),
        case_id=expired.id,
        mode="O2",
        status="completed",
        budget={},
    )
    new_scan = Scan(
        id=str(uuid.uuid4()),
        case_id=fresh.id,
        mode="O2",
        status="completed",
        budget={},
    )
    session.add_all([old_scan, new_scan])
    await session.flush()

    session.add_all(
        [
            # Suresi dolmus vaka: e-posta her iki yerde de var
            Task(
                id=str(uuid.uuid4()),
                scan_id=old_scan.id,
                connector="github_email",
                platform="github",
                kind="commits_by_email",
                params={"email": "eski@example.com", "email_tag": "sha256:abc"},
            ),
            Seed(
                id=str(uuid.uuid4()),
                case_id=expired.id,
                platform="github",
                username="ghuser",
                email="seed-eski@example.com",
                source="manual",
            ),
            Entity(
                id=str(uuid.uuid4()),
                case_id=expired.id,
                type="account",
                platform="github",
                label="ghuser",
                attributes={
                    "username": "ghuser",
                    "public_email": "eski@example.com",
                    "email": "attr-eski@example.com",
                    "name": "Eski Kisi",
                },
            ),
            # Guncel vaka: dokunulmamali
            Task(
                id=str(uuid.uuid4()),
                scan_id=new_scan.id,
                connector="github_email",
                platform="github",
                kind="commits_by_email",
                params={"email": "yeni@example.com", "email_tag": "sha256:def"},
            ),
            Seed(
                id=str(uuid.uuid4()),
                case_id=fresh.id,
                platform="github",
                username="yeniuser",
                email="seed-yeni@example.com",
                source="manual",
            ),
            Entity(
                id=str(uuid.uuid4()),
                case_id=fresh.id,
                type="account",
                platform="github",
                label="yeniuser",
                attributes={
                    "username": "yeniuser",
                    "public_email": "yeni@example.com",
                    "email": "attr-yeni@example.com",
                },
            ),
        ]
    )
    await session.commit()

    result = await run_retention_purge(session)
    # 1 task + 1 seed + 1 entity = 3 kayit temizlendi
    assert result["pii_redacted"] == 3, result

    # Z5 N4: taze okuma (identity map'i bosalt) -> gercekten DB'ye yazildi mi?
    session.expunge_all()

    task_rows = (await session.execute(select(Task))).scalars().all()
    seed_rows = {s.username: s for s in (await session.execute(select(Seed))).scalars().all()}
    entities = {e.label: e for e in (await session.execute(select(Entity))).scalars().all()}

    # Suresi dolmus vakada ham e-posta yok, diger alanlar duruyor
    old_task = next(t for t in task_rows if t.scan_id == old_scan.id)
    assert "email" not in old_task.params
    assert old_task.params["email_tag"] == "sha256:abc"
    assert "public_email" not in entities["ghuser"].attributes
    assert "email" not in entities["ghuser"].attributes  # Z5 I2
    assert entities["ghuser"].attributes["username"] == "ghuser"
    assert entities["ghuser"].attributes["name"] == "Eski Kisi"
    assert seed_rows["ghuser"].email is None  # Z5 I2: seeds.email

    # Guncel vaka etkilenmedi
    new_task = next(t for t in task_rows if t.scan_id == new_scan.id)
    assert new_task.params["email"] == "yeni@example.com"
    assert entities["yeniuser"].attributes["public_email"] == "yeni@example.com"
    assert entities["yeniuser"].attributes["email"] == "attr-yeni@example.com"
    assert seed_rows["yeniuser"].email == "seed-yeni@example.com"

    # DB'de ham e-posta kalmadi (suresi dolmus vaka icin)
    raw = str(
        [t.params for t in task_rows]
        + [e.attributes for e in entities.values()]
        + [s.email for s in seed_rows.values()]
    )
    assert "eski@example.com" not in raw
    assert "seed-eski@example.com" not in raw
    assert "attr-eski@example.com" not in raw
    # email_tag gibi turetilmis alanlar korunur
    assert old_task.params["email_tag"] == "sha256:abc"

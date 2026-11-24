"""Kanıt arşivi: snapshot kaydı, uçlar, saklama süresi ve atıf bağlama."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone

import pytest
import test_research_workspace as _workspace
from showis.db.models import Case as DbCase
from showis.db.models import EvidenceSnapshot, ResearchSession
from showis.services.evidence_archive import (
    MAX_SNAPSHOT_TEXT,
    save_snapshot,
    snapshot_ids_for_urls,
)
from showis.services.research import FetchResult, ResearchRuntime, _archive_citations
from showis.services.research_playbook import build_identity_profile
from showis.services.retention import purge_expired_snapshots
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory


async def _case(db, case_id: str = "case-ev") -> DbCase:
    case = DbCase(
        id=case_id,
        name="Kanıt",
        purpose="test",
        legal_basis="self_check",
        status="active",
        retention_days=30,
    )
    db.add(case)
    await db.flush()
    return case


@pytest.mark.asyncio
async def test_save_snapshot_dedupes_and_truncates(db_factory):
    async with db_factory() as db:
        case = await _case(db)
        big = "a" * (MAX_SNAPSHOT_TEXT + 500)
        first = await save_snapshot(
            db, case=case, url="https://example.org/p", title="T", text=big, sha256="f" * 64
        )
        again = await save_snapshot(
            db, case=case, url="https://example.org/p", title="T", text=big, sha256="f" * 64
        )
        changed = await save_snapshot(
            db, case=case, url="https://example.org/p", title="T", text="yeni", sha256="e" * 64
        )
        await db.commit()
        assert again.id == first.id
        assert changed.id != first.id
        assert first.text_truncated is True
        assert len(first.text) == MAX_SNAPSHOT_TEXT
        assert first.text_sha256 == hashlib.sha256(first.text.encode()).hexdigest()
        assert first.expires_at is not None
        ids = await snapshot_ids_for_urls(db, case.id, ["https://example.org/p", "https://x.y"])
        assert ids == {"https://example.org/p": changed.id}


@pytest.mark.asyncio
async def test_snapshot_endpoints_scope_and_integrity(client, db_factory):
    async with db_factory() as db:
        case = await _case(db)
        other = await _case(db, "case-other")
        mine = await save_snapshot(
            db, case=case, url="https://example.org/a", title="Alfa", text="merhaba", sha256=""
        )
        theirs = await save_snapshot(
            db, case=other, url="https://example.org/b", title="Beta", text="gizli", sha256=""
        )
        await db.commit()
        mine_id, theirs_id = mine.id, theirs.id

    listing = await client.get("/api/v1/cases/case-ev/evidence/snapshots")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1 and body["items"][0]["id"] == mine_id
    assert "text" not in body["items"][0] and body["items"][0]["excerpt"] == "merhaba"

    found = await client.get("/api/v1/cases/case-ev/evidence/snapshots", params={"q": "alfa"})
    assert found.json()["total"] == 1
    missing = await client.get("/api/v1/cases/case-ev/evidence/snapshots", params={"q": "zzz"})
    assert missing.json()["total"] == 0

    detail = await client.get(f"/api/v1/cases/case-ev/evidence/snapshots/{mine_id}")
    assert detail.status_code == 200
    assert detail.json()["text"] == "merhaba" and detail.json()["integrity_ok"] is True

    # Başka vakanın snapshot'ı bu vaka üzerinden okunamaz.
    assert (
        await client.get(f"/api/v1/cases/case-ev/evidence/snapshots/{theirs_id}")
    ).status_code == 404
    assert (await client.get("/api/v1/cases/yok/evidence/snapshots")).status_code == 404

    # Metin sonradan değişirse bütünlük işareti düşer.
    async with db_factory() as db:
        row = (
            await db.execute(select(EvidenceSnapshot).where(EvidenceSnapshot.id == mine_id))
        ).scalar_one()
        row.text = "değiştirildi"
        await db.commit()
    tampered = await client.get(f"/api/v1/cases/case-ev/evidence/snapshots/{mine_id}")
    assert tampered.json()["integrity_ok"] is False


@pytest.mark.asyncio
async def test_purge_expired_snapshots(db_factory):
    async with db_factory() as db:
        case = await _case(db)
        old = await save_snapshot(
            db, case=case, url="https://example.org/old", title="", text="x", sha256=""
        )
        fresh = await save_snapshot(
            db, case=case, url="https://example.org/new", title="", text="y", sha256=""
        )
        old.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()
        fresh_id = fresh.id
        assert await purge_expired_snapshots(db) == 1
        left = (await db.execute(select(EvidenceSnapshot.id))).scalars().all()
        assert left == [fresh_id]


async def _runtime(db, case: DbCase, *, offline: bool = True) -> ResearchRuntime:
    session = ResearchSession(
        id="sess-ev", case_id=case.id, title="t", question="q", mode="research"
    )
    db.add(session)
    await db.commit()
    return ResearchRuntime(
        db=db,
        session=session,
        case=case,
        target=None,
        client=None,  # type: ignore[arg-type]
        identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
        pool_items=[],
        stats={"depth": "quick", "offline": offline},
        started=time.monotonic(),
    )


@pytest.mark.asyncio
async def test_archive_page_and_citation_link(db_factory):
    async with db_factory() as db:
        case = await _case(db)
        runtime = await _runtime(db, case)
        page = FetchResult(
            url="https://example.org/okunan", title="Okunan", text="içerik", digest="d" * 64
        )
        snapshot_id = await runtime.archive_page(page, source="research")
        assert snapshot_id and runtime.stats["snapshots"] == 1

        citations = [
            {"label": "a", "url": "https://example.org/okunan", "snapshot_id": None},
            {"label": "b", "url": "https://example.org/okunmadi", "snapshot_id": None},
            {"label": "c", "url": None, "snapshot_id": None},
        ]
        linked = await _archive_citations(runtime, citations)
        # offline: okunmamış sayfa ağdan çekilmez, yalnız bilinen snapshot bağlanır.
        assert linked[0]["snapshot_id"] == snapshot_id
        assert linked[1]["snapshot_id"] is None and linked[2]["snapshot_id"] is None


@pytest.mark.asyncio
async def test_archive_failure_does_not_break_research(db_factory, monkeypatch):
    async with db_factory() as db:
        case = await _case(db)
        runtime = await _runtime(db, case)

        async def boom(*args, **kwargs):
            raise RuntimeError("disk")

        monkeypatch.setattr("showis.services.research.save_snapshot", boom)
        page = FetchResult(url="https://example.org/z", title="", text="t")
        assert await runtime.archive_page(page, source="research") is None
        assert any("arşiv" in err for err in runtime.errors)

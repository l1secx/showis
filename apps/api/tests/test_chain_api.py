"""Zincir API ucu ve havuzun vaka grafiğine katılması (ağ yok)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Entity as DbEntity
from showis.db.session import get_db
from showis.main import app
from showis.services import chain as chain_mod
from showis.services.chain import ChainEngine, PageData
from showis.services.pool import create_item
from showis.services.reasoning.graph import upsert_relation
from showis_connectors.account_intel import AccountIntel, AccountLink
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

CASE_ID = "case-chain-api"


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.fixture
async def client(db_factory):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _case(factory) -> tuple[str, str]:
    async with factory() as db:
        db.add(DbCase(id=CASE_ID, name="z", purpose="p", legal_basis="self_check"))
        await db.flush()
        anchor, _ = await create_item(
            db,
            case_id=CASE_ID,
            kind="url",
            label="GitHub",
            value="https://github.com/l1sec",
            source="user",
        )
        rejected, _ = await create_item(
            db,
            case_id=CASE_ID,
            kind="url",
            label="yanlış",
            value="https://x.com/baskasi",
            source="ai",
            status="proposed",
        )
        rejected.status = "rejected"
        await db.commit()
        return anchor.id, rejected.id


@pytest.mark.asyncio
async def test_chain_endpoint_writes_proposals(client, db_factory, monkeypatch):
    anchor_id, rejected_id = await _case(db_factory)

    async def intel(network: str, handle: str):
        if (network, handle) == ("github", "l1sec"):
            return AccountIntel(
                platform="github",
                handle="l1sec",
                profile_url="https://github.com/l1sec",
                links=[AccountLink("https://linktr.ee/l1sec", "owner")],
            )
        return None

    async def page(url: str, trusted: bool):
        if url == "https://linktr.ee/l1sec":
            return PageData(url=url, links=["https://github.com/l1sec"])
        return None

    async def fake_build_engine(db, *, max_fetches=30, max_depth=3, **kwargs):
        return ChainEngine(
            intel_fetcher=intel, page_fetcher=page, max_fetches=max_fetches, max_depth=max_depth
        )

    monkeypatch.setattr(chain_mod, "build_engine", fake_build_engine)
    res = await client.post(f"/api/v1/cases/{CASE_ID}/reasoning/chain", json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["anchors"] >= 1
    assert body["created"] >= 1
    linktree = next(n for n in body["nodes"] if n["network"] == "linktr.ee")
    assert linktree["trust"] >= 0.99
    assert {e["relation"] for e in linktree["evidence"]} >= {"owner", "backlink"}

    pool = (await client.get(f"/api/v1/cases/{CASE_ID}/pool?limit=200")).json()["items"]
    created = [item for item in pool if item["value"] == "https://linktr.ee/l1sec"]
    assert created and created[0]["status"] == "proposed"

    # Reddedilmiş kayıttan zincir kurulamaz.
    res = await client.post(
        f"/api/v1/cases/{CASE_ID}/reasoning/chain", json={"item_ids": [rejected_id]}
    )
    assert res.status_code == 422

    # Zincir pivotu da listelenir.
    pivots = (
        await client.get(f"/api/v1/cases/{CASE_ID}/reasoning/items/{anchor_id}/pivots")
    ).json()["items"]
    assert any(p["id"] == "chain_expand" for p in pivots)


@pytest.mark.asyncio
async def test_case_graph_includes_pool_nodes_relations_and_scan_link(client, db_factory):
    anchor_id, rejected_id = await _case(db_factory)
    async with db_factory() as db:
        linked, _ = await create_item(
            db,
            case_id=CASE_ID,
            kind="url",
            label="Linktree",
            value="https://linktr.ee/l1sec",
            source="ai",
            status="proposed",
            confidence=0.97,
        )
        note, _ = await create_item(
            db, case_id=CASE_ID, kind="note", label="not", value="uzun not metni", source="user"
        )
        await upsert_relation(
            db,
            case_id=CASE_ID,
            src_item_id=anchor_id,
            dst_item_id=linked.id,
            type="linked_from",
            confidence=0.95,
            evidence=[{"rule": "chain:owner", "source": "s", "weight": 3.8, "note": "blog alanı"}],
            source="pivot",
        )
        db.add(
            DbEntity(
                id="ent-gh",
                case_id=CASE_ID,
                type="account",
                label="github:l1sec",
                platform="github",
                url="https://github.com/l1sec/",
            )
        )
        await db.commit()
        linked_id, note_id = linked.id, note.id

    payload = (await client.get(f"/api/v1/cases/{CASE_ID}/graph")).json()
    nodes = {n["data"]["id"]: n["data"] for n in payload["nodes"]}
    assert nodes[anchor_id]["origin"] == "pool" and nodes[anchor_id]["score"] == 1.0
    assert nodes[linked_id]["status"] == "proposed"
    assert rejected_id not in nodes  # reddedilen gösterilmez
    assert note_id not in nodes  # not metni düğüm değildir
    assert nodes["ent-gh"]["origin"] == "scan"
    edges = {
        (e["data"]["source"], e["data"]["target"], e["data"]["type"]) for e in payload["edges"]
    }
    assert (anchor_id, linked_id, "linked_from") in edges
    assert ("ent-gh", anchor_id, "same_as") in edges

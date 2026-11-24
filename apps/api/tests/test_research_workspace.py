"""Araştırma havuzu, AI sağlayıcı profilleri, ajan döngüsü ve tedarik zinciri."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import (
    AIProvider,
    PoolItem,
    ResearchMessage,
    ResearchSession,
    SupplyChainEdge,
    SupplyChainNode,
)
from showis.db.models import (
    Case as DbCase,
)
from showis.db.models import (
    Seed as DbSeed,
)
from showis.db.models import (
    Target as DbTarget,
)
from showis.db.session import get_db
from showis.main import app
from showis.services import ai_providers as ai_service
from showis.services.pool import create_item, seed_pool_for_case
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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
async def client(db_factory, monkeypatch):
    monkeypatch.setattr("showis.api.v1.research.run_research_session", lambda *args, **kwargs: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def _seed_case(factory) -> str:
    async with factory() as session:
        case = DbCase(
            id="case-pool",
            name="Havuz Testi",
            purpose="Kendi hesaplarımı doğrulamak",
            legal_basis="self_check",
            notes="İlk not",
            status="active",
            retention_days=30,
        )
        session.add(case)
        await session.flush()
        session.add(
            DbTarget(
                case_id=case.id,
                kind="handle",
                first_name="Kerem",
                last_name="Boz",
                nickname="keremboz",
                email="kerem@example.com",
                hints={"city": "Ankara", "company": "Acme"},
            )
        )
        session.add(
            DbSeed(
                case_id=case.id,
                platform="github",
                username="keremboz",
                url="https://github.com/keremboz",
            )
        )
        await session.commit()
        return case.id


@pytest.mark.asyncio
async def test_pool_seed_dedupe_and_approval(db_factory, client):
    case_id = await _seed_case(db_factory)
    response = await client.get(f"/api/v1/cases/{case_id}/pool")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 7
    kinds = {item["kind"] for item in data["items"]}
    assert {"name", "username", "email", "location", "organization", "note", "url"} <= kinds
    assert all(item["source"] == "user" for item in data["items"])

    # Aynı değer ikinci kez eklenirse yeni satır üretilmez.
    first = await client.post(
        f"/api/v1/cases/{case_id}/pool",
        json={"kind": "email", "label": "Ek e-posta", "value": "Kerem@Example.com"},
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/v1/cases/{case_id}/pool",
        json={"kind": "email", "label": "Tekrar", "value": "kerem@example.com"},
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["label"] == "Tekrar"

    # AI önerisi onay bekler; kullanıcı onayı durumu değiştirir.
    async with db_factory() as session:
        item, _ = await create_item(
            session,
            case_id=case_id,
            kind="organization",
            label="AI önerisi",
            value="Beta Lojistik",
            source="ai",
            status="proposed",
        )
        await session.commit()
        item_id = item.id
    listed = await client.get(f"/api/v1/cases/{case_id}/pool?status=proposed")
    assert any(row["id"] == item_id for row in listed.json()["items"])
    approved = await client.patch(
        f"/api/v1/cases/{case_id}/pool/{item_id}",
        json={"status": "confirmed"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "confirmed"

    deleted = await client.delete(f"/api/v1/cases/{case_id}/pool/{item_id}")
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_pool_event_keeps_timeline_attributes(db_factory, client):
    case_id = await _seed_case(db_factory)
    created = await client.post(
        f"/api/v1/cases/{case_id}/pool",
        json={
            "kind": "event",
            "label": "Konferans konuşması",
            "value": "2024 ODTÜ kariyer gününde konuşma",
            "attributes": {
                "date": "2024-05-12",
                "place": "Ankara",
                "organization": "ODTÜ",
                "source_url": "https://example.com/etkinlik",
            },
            "provenance": {"url": "https://example.com/etkinlik"},
        },
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["kind"] == "event"
    assert item["attributes"]["place"] == "Ankara"
    assert item["attributes"]["organization"] == "ODTÜ"

    listed = await client.get(f"/api/v1/cases/{case_id}/pool?kind=event")
    assert listed.status_code == 200
    assert any(row["id"] == item["id"] for row in listed.json()["items"])


@pytest.mark.asyncio
async def test_pool_hibp_breach_check(db_factory, client, monkeypatch):
    monkeypatch.setenv("HIBP_API_KEY", "hibp-test-key")
    case_id = await _seed_case(db_factory)
    created = await client.post(
        f"/api/v1/cases/{case_id}/pool",
        json={"kind": "email", "label": "Leak testi", "value": "leak@example.com"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]

    with respx.mock(assert_all_called=True) as mock:
        mock.get(url__regex=r"https://haveibeenpwned\.com/api/v3/breachedaccount/.*").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "Name": "AcmeLeak",
                        "Title": "Acme Leak",
                        "Domain": "acme.example",
                        "BreachDate": "2024-01-02",
                        "PwnCount": 1000,
                        "DataClasses": ["Email addresses", "Passwords"],
                        "Description": "<p>Acme <a href='#'>veri sızıntısı</a></p>",
                        "IsVerified": True,
                        "IsSensitive": False,
                        "IsSpamList": False,
                        "IsMalware": False,
                    }
                ],
            )
        )
        response = await client.post(
            f"/api/v1/cases/{case_id}/pool/{item_id}/breach-check"
        )

    assert response.status_code == 200, response.text
    breach = response.json()["breach"]
    assert breach["breached"] is True
    assert breach["count"] == 1
    assert breach["breaches"][0]["title"] == "Acme Leak"
    assert "Passwords" in breach["data_classes"]
    assert "<p>" not in breach["breaches"][0]["description"]

    listed = await client.get(f"/api/v1/cases/{case_id}/pool?kind=email")
    row = next(item for item in listed.json()["items"] if item["id"] == item_id)
    assert row["attributes"]["breach_check"]["count"] == 1


@pytest.mark.asyncio
async def test_pool_comment_is_added(db_factory, client):
    case_id = await _seed_case(db_factory)
    response = await client.post(
        f"/api/v1/cases/{case_id}/comments",
        json={"body": "Tedarikçi görüşmesi 12 Aralık", "author": "analist"},
    )
    assert response.status_code == 201
    async with db_factory() as session:
        notes = (
            (
                await session.execute(
                    select(PoolItem).where(
                        PoolItem.case_id == case_id, PoolItem.label == "Yorum — analist"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert notes and "Tedarikçi" in notes[0].value


@pytest.mark.asyncio
async def test_ai_provider_profiles_activate_and_secret_invalidation(db_factory, client):
    created = await client.post(
        "/api/v1/ai/providers",
        json={
            "name": "OpenCode Go",
            "preset": "opencode-go",
            "config": {
                "base_url": "https://opencode.ai/inference/go/openai/v1",
                "model": "minimax-m3.1-flash",
            },
            "secret": "sk-test-secret",
            "activate": True,
        },
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["is_active"] is True
    assert first["secret_masked"] and "sk-test-secret" not in str(first)

    second = await client.post(
        "/api/v1/ai/providers",
        json={
            "name": "OpenAI",
            "preset": "openai",
            "config": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
            "secret": "sk-second",
        },
    )
    assert second.status_code == 201
    activated = await client.post(f"/api/v1/ai/providers/{second.json()['id']}/activate")
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    async with db_factory() as session:
        providers = (await session.execute(select(AIProvider))).scalars().all()
        assert sum(1 for provider in providers if provider.is_active) == 1

    # base_url değişince sır geçersiz kılınır.
    changed = await client.patch(
        f"/api/v1/ai/providers/{first['id']}",
        json={
            "config": {
                "base_url": "https://api.openai.com/v1",
                "model": "minimax-m3.1-flash",
            }
        },
    )
    assert changed.status_code == 200
    assert changed.json()["secret_required"] is True
    assert changed.json()["secret_masked"] is None


@pytest.mark.asyncio
async def test_ai_provider_model_discovery(client):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-4o-mini"},
                        {"id": "gpt-4.1"},
                        {"id": "gpt-4.1"},
                    ]
                },
            )
        )
        response = await client.post(
            "/api/v1/ai/providers/discover",
            json={
                "base_url": "https://api.openai.com/v1",
                "secret": "sk-test",
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["gpt-4.1", "gpt-4o-mini"]


@pytest.mark.asyncio
async def test_build_llm_client_prefers_active_profile(db_factory):
    async with db_factory() as session:
        provider = AIProvider(
            name="Test Profil",
            preset="openai",
            config={
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o-mini",
                "models": ["gpt-4o-mini", "gpt-4.1"],
            },
            secret_encrypted=ai_service.encrypt_secret("sk-test"),
            secret_masked="sk-...test",
            enabled=True,
            is_active=True,
        )
        session.add(provider)
        await session.commit()

        from showis.services.connectors_factory import build_llm_client

        client = await build_llm_client(session)
        assert client is not None
        assert client.model == "gpt-4o-mini"

        override = await build_llm_client(session, model_override="gpt-4.1")
        assert override is not None
        assert override.model == "gpt-4.1"
        disallowed = await build_llm_client(session, model_override="not-allowed")
        assert disallowed is not None
        assert disallowed.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_supply_chain_nodes_edges_and_graph(db_factory, client):
    case_id = await _seed_case(db_factory)
    supplier = await client.post(
        f"/api/v1/cases/{case_id}/supply-chain/nodes",
        json={"kind": "supplier", "name": "Beta Lojistik", "country": "Türkiye"},
    )
    company = await client.post(
        f"/api/v1/cases/{case_id}/supply-chain/nodes",
        json={"kind": "company", "name": "Acme", "country": "Türkiye"},
    )
    assert supplier.status_code == 201 and company.status_code == 201

    edge = await client.post(
        f"/api/v1/cases/{case_id}/supply-chain/edges",
        json={
            "source_id": supplier.json()["id"],
            "target_id": company.json()["id"],
            "relation": "supplies",
            "label": "2026 sözleşmesi",
        },
    )
    assert edge.status_code == 201, edge.text

    graph = await client.get(f"/api/v1/cases/{case_id}/supply-chain")
    assert graph.status_code == 200
    assert len(graph.json()["nodes"]) == 2
    assert len(graph.json()["edges"]) == 1

    approved = await client.patch(
        f"/api/v1/cases/{case_id}/supply-chain/edges/{edge.json()['id']}",
        json={"status": "confirmed"},
    )
    assert approved.status_code == 200

    removed = await client.delete(
        f"/api/v1/cases/{case_id}/supply-chain/nodes/{company.json()['id']}"
    )
    assert removed.status_code == 204
    async with db_factory() as session:
        remaining_edges = (
            (
                await session.execute(
                    select(SupplyChainEdge).where(SupplyChainEdge.case_id == case_id)
                )
            )
            .scalars()
            .all()
        )
        remaining_nodes = (
            (
                await session.execute(
                    select(SupplyChainNode).where(SupplyChainNode.case_id == case_id)
                )
            )
            .scalars()
            .all()
        )
    assert remaining_edges == []
    assert len(remaining_nodes) == 1


@pytest.mark.asyncio
async def test_research_without_provider_is_rejected(db_factory, client, monkeypatch):
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    case_id = await _seed_case(db_factory)
    response = await client.post(
        f"/api/v1/cases/{case_id}/research",
        json={"question": "Sağlayıcısız araştırma denemesi", "mode": "research"},
    )
    assert response.status_code == 422
    assert "sağlayıcı" in response.text.lower() or "provider" in response.text.lower()


@pytest.mark.asyncio
async def test_research_session_start_and_detail(db_factory, client):
    case_id = await _seed_case(db_factory)
    provider = await client.post(
        "/api/v1/ai/providers",
        json={
            "name": "OpenCode Go",
            "preset": "opencode-go",
            "config": {"base_url": "https://opencode.ai/inference/go/openai/v1"},
            "secret": "sk-test",
            "activate": True,
        },
    )
    assert provider.status_code == 201

    started = await client.post(
        f"/api/v1/cases/{case_id}/research",
        json={
            "question": "Havuzdaki kimlikleri kamuya açık kaynaklarda doğrula.",
            "mode": "research",
        },
    )
    assert started.status_code == 201, started.text
    session_id = started.json()["id"]

    detail = await client.get(f"/api/v1/cases/{case_id}/research/{session_id}")
    assert detail.status_code == 200
    messages = detail.json()["messages"]
    assert [message["role"] for message in messages] == ["user"]
    assert messages[0]["content"].startswith("Havuzdaki")

    # Çalışan oturum varken ikinci oturum 409.
    conflict = await client.post(
        f"/api/v1/cases/{case_id}/research",
        json={"question": "İkinci araştırma sorusu denemesi", "mode": "research"},
    )
    assert conflict.status_code == 409

    cancelled = await client.post(f"/api/v1/cases/{case_id}/research/{session_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


class _FakeLLM:
    def __init__(self, steps):
        self.steps = list(steps)
        self.model = "fake"

    async def complete(self, **kwargs):
        return self.steps.pop(0)


@pytest.mark.asyncio
async def test_agent_loop_searches_pool_and_finishes(db_factory):
    case_id = await _seed_case(db_factory)
    async with db_factory() as session:
        case = (await session.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
        target = (
            await session.execute(select(DbTarget).where(DbTarget.case_id == case_id))
        ).scalar_one()
        seeds = (
            (await session.execute(select(DbSeed).where(DbSeed.case_id == case_id))).scalars().all()
        )
        await seed_pool_for_case(session, case=case, target=target, seeds=seeds)
        await session.commit()

        session_row = ResearchSession(
            case_id=case_id,
            title="Ajan testi",
            question="Havuzda keremboz var mı?",
            mode="research",
            status="running",
            created_by="test",
        )
        session.add(session_row)
        await session.flush()
        session.add(
            ResearchMessage(
                session_id=session_row.id,
                role="user",
                content="Havuzda keremboz var mı?",
            )
        )
        await session.commit()
        session_id = session_row.id

        from showis.services.research import AgentAction, AgentStep, _run_loop

        fake = _FakeLLM(
            [
                AgentStep(
                    thought="Önce havuzu tara.",
                    action=AgentAction(tool="search_pool", input={"query": "keremboz"}),
                ),
                AgentStep(
                    thought="Kanıt bulundu.",
                    final="Havuzda keremboz kullanıcı adı kayıtlı.",
                    citations=[
                        {
                            "label": "Havuz kaydı",
                            "url": None,
                            "pool_item_id": None,
                        }
                    ],
                ),
            ]
        )
        stats: dict[str, Any] = {}
        await _run_loop(
            session,
            session=session_row,
            case=case,
            target=target,
            client=fake,  # type: ignore[arg-type]
            history=[
                ResearchMessage(
                    session_id=session_id,
                    role="user",
                    content="Havuzda keremboz var mı?",
                )
            ],
            stats=stats,
        )
        await session.refresh(session_row)
        assert session_row.status == "completed"
        assert "keremboz" in (session_row.final_answer or "")
        assert session_row.stats["tool_calls"] == 1
        assert session_row.stats["activity"] == "Nihai sentez yazılıyor"
        messages = (
            (
                await session.execute(
                    select(ResearchMessage)
                    .where(ResearchMessage.session_id == session_id)
                    .order_by(ResearchMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert any(
            message.role == "tool" and message.tool_name == "search_pool" for message in messages
        )

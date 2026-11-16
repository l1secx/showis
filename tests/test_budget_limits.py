"""SEC-05 — tarama bütçesi istemci tarafından override edilemez + tek aktif tarama."""

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from showis.api.v1.cases import ScanStartRequest, _clamp_budget
from showis.db.base import Base
from showis.db.models import Scan
from showis.db.session import get_db
from showis.main import app
from showis_engine.scan_modes import budget_for_mode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.fixture
async def client(session_factory, monkeypatch):
    # T-108 (SEC-01): API artik token ister.
    monkeypatch.setenv("SHOWIS_API_TOKEN", "test-token")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Token": "test-token"},
    ) as http_client:
        yield http_client


@pytest.fixture(autouse=True)
def _no_background_scan(monkeypatch):
    async def _noop(scan_id: str) -> None:
        return None

    monkeypatch.setattr("showis.api.v1.cases.run_scan_in_background", _noop)


async def _create_case(client) -> str:
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": "Butce Testi",
            "purpose": "Kendi hesabimi denetlemek icin",
            "legal_basis": "self_check",
            "target": {"first_name": "Kerem", "last_name": "Boz", "hints": {}},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_clamp_budget_mod_profilini_asmaz():
    profile = budget_for_mode("O2")
    clamped = _clamp_budget(
        {
            "max_tasks": 100000,
            "max_http_requests": 1000000,
            "max_wall_seconds": 86400,
            "max_llm_calls": 999,
            "bilinmeyen": 5,
        },
        "O2",
    )
    assert clamped["max_tasks"] == profile["max_tasks"]
    assert clamped["max_http_requests"] == profile["max_http_requests"]
    assert clamped["max_wall_seconds"] == profile["max_wall_seconds"]
    assert clamped["max_llm_calls"] == profile["max_llm_calls"]
    assert "bilinmeyen" not in clamped


def test_clamp_budget_daha_kucuk_degeri_korur():
    clamped = _clamp_budget({"max_tasks": 10}, "O2")
    assert clamped["max_tasks"] == 10
    assert clamped["max_http_requests"] == budget_for_mode("O2")["max_http_requests"]


def test_clamp_budget_gecersiz_degerleri_yok_sayar():
    profile = budget_for_mode("O1")
    clamped = _clamp_budget(
        {"max_tasks": -5, "max_http_requests": "cok", "max_wall_seconds": True}, "O1"
    )
    assert clamped == profile


def test_clamp_budget_none_profil_dondurur():
    assert _clamp_budget(None, "O3") == budget_for_mode("O3")


def test_scan_request_gecersiz_mod_reddedilir():
    with pytest.raises(ValidationError):
        ScanStartRequest(mode="O9")


@pytest.mark.asyncio
async def test_scan_butcesi_veritabaninda_kirpilmis(client, session_factory):
    case_id = await _create_case(client)
    res = await client.post(
        f"/api/v1/cases/{case_id}/scan",
        json={
            "mode": "O3",
            "budget": {
                "max_tasks": 100000,
                "max_http_requests": 1000000,
                "max_wall_seconds": 86400,
            },
        },
    )
    assert res.status_code == 200, res.text
    scan_id = res.json()["scan_id"]

    async with session_factory() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == scan_id))
        ).scalar_one()
    profile = budget_for_mode("O3")
    assert scan.budget["max_tasks"] == profile["max_tasks"]
    assert scan.budget["max_http_requests"] == profile["max_http_requests"]
    assert scan.budget["max_wall_seconds"] == profile["max_wall_seconds"]


@pytest.mark.asyncio
async def test_aktif_tarama_varken_ikinci_istek_409(client):
    case_id = await _create_case(client)
    first = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    assert first.status_code == 200

    second = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "scan_already_active"


@pytest.mark.asyncio
async def test_bilinmeyen_budget_alanlari_yok_sayilir(client, session_factory):
    case_id = await _create_case(client)
    res = await client.post(
        f"/api/v1/cases/{case_id}/scan",
        json={"mode": "O1", "budget": {"max_cost_usd": 9999, "serbest": "x"}},
    )
    assert res.status_code == 200
    async with session_factory() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == res.json()["scan_id"]))
        ).scalar_one()
    assert scan.budget["max_cost_usd"] == budget_for_mode("O1")["max_cost_usd"]
    assert "serbest" not in scan.budget

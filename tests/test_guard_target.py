"""SEC-07 (guard_target canlı) ve SEC-14 (girdi doğrulama) regresyon testleri."""

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import AuditLog, Case
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.main import app
from showis_contracts import Target, VariantSet
from showis_engine.planner import QueryPlanner
from showis_engine.policy import PolicyViolationError, guard_target
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


def _case_payload(**overrides) -> dict:
    payload = {
        "name": "AUP Testi",
        "purpose": "Kendi hesabimi denetlemek icin",
        "legal_basis": "self_check",
        "target": {"first_name": "Kerem", "last_name": "Boz", "hints": {}},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# guard_target — birim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hint",
    [
        "11. sınıf öğrencisi",
        "lise öğrencisi",
        "16 yaşında",
        "underage",
    ],
)
def test_guard_target_resit_olmayan_sinyali_yakalar(hint):
    target = Target(first_name="Ali", last_name="Veli", hints={"extra": hint})
    guarded = guard_target(target)
    assert guarded.review_required is True


def test_guard_target_temiz_hedefi_gecirir():
    target = Target(first_name="Kerem", last_name="Boz", hints={"city": "Ankara"})
    assert guard_target(target).review_required is False


@pytest.mark.asyncio
async def test_planner_inceleme_gerektiren_hedef_icin_sorgu_uretmez():
    target = Target(
        first_name="Ali",
        last_name="Veli",
        hints={"extra": "lise öğrencisi"},
    )
    with pytest.raises(PolicyViolationError):
        await QueryPlanner().plan(target, VariantSet(variants=[]))


@pytest.mark.asyncio
async def test_planner_temiz_hedef_icin_sorgu_uretir():
    target = Target(first_name="Kerem", last_name="Boz", hints={"school": "ODTÜ"})
    plan = await QueryPlanner().plan(target, VariantSet(variants=[]))
    assert plan.queries


# ---------------------------------------------------------------------------
# Kayıt yolu — API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_create_resit_olmayan_hedef_422_ve_audit(client, session_factory):
    payload = _case_payload()
    payload["target"]["hints"] = {"extra": "lise öğrencisi"}

    res = await client.post("/api/v1/cases", json=payload)
    assert res.status_code == 422
    assert "reşit olmayan" in res.json()["detail"]

    async with session_factory() as session:
        cases = (await session.execute(select(Case))).scalars().all()
        logs = (await session.execute(select(AuditLog))).scalars().all()

    assert cases == [], "reddedilen hedef için vaka yazılmamalı"
    assert [log.action for log in logs] == ["target.blocked"]
    details = logs[0].details
    assert details["reason"] == "review_required"
    assert "lise" not in str(details), "audit'e ham ipucu yazılmamalı"


@pytest.mark.asyncio
async def test_scan_start_inceleme_bayrakli_hedefi_reddeder(client, session_factory):
    res = await client.post("/api/v1/cases", json=_case_payload())
    assert res.status_code == 201
    case_id = res.json()["id"]

    # Eski/elle yazılmış kayıt: target.review_required=True
    async with session_factory() as session:
        db_target = (
            await session.execute(select(DbTarget).where(DbTarget.case_id == case_id))
        ).scalar_one()
        db_target.review_required = True
        await session.commit()

    scan_res = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    assert scan_res.status_code == 422

    async with session_factory() as session:
        logs = (await session.execute(select(AuditLog))).scalars().all()
    assert any(
        log.action == "target.blocked" and (log.details or {}).get("stage") == "scan.start"
        for log in logs
    )


@pytest.mark.asyncio
async def test_review_pending_durumundaki_vaka_taranmaz(client, session_factory):
    res = await client.post("/api/v1/cases", json=_case_payload())
    case_id = res.json()["id"]
    async with session_factory() as session:
        case = (await session.execute(select(Case).where(Case.id == case_id))).scalar_one()
        case.status = "review_pending"
        await session.commit()

    scan_res = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O1"})
    assert scan_res.status_code == 422


# ---------------------------------------------------------------------------
# SEC-14 — girdi doğrulama
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"retention_days": 100000}),
        lambda p: p.update({"retention_days": 0}),
        lambda p: p.update({"retention_days": -5}),
        lambda p: p.update({"legal_basis": "istemedim"}),
        lambda p: p.update({"name": "x" * 300}),
        lambda p: p.update({"purpose": "kisa"}),
        lambda p: p.update({"notes": "x" * 5000}),
        lambda p: p["target"].update({"first_name": "K"}),
        lambda p: p["target"].update({"last_name": ""}),
        lambda p: p["target"].update({"hints": {"bilinmeyen": "x"}}),
        lambda p: p["target"].update({"hints": {"school": "x" * 300}}),
        lambda p: p["target"].update({"hints": {"school": {"nested": 1}}}),
        lambda p: p.update({"seeds": [{"platform": "github", "username": "a?b#c"}]}),
        lambda p: p.update({"seeds": [{"platform": "github", "email": "bozuk-eposta"}]}),
        lambda p: p.update({"seeds": [{"platform": "bilinmeyen-platform"}]}),
        lambda p: p.update({"seeds": [{"platform": "github", "url": "file:///etc/passwd"}]}),
    ],
)
async def test_gecersiz_girdi_422(client, mutate):
    payload = _case_payload()
    payload["seeds"] = []
    mutate(payload)
    res = await client.post("/api/v1/cases", json=payload)
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_gecerli_girdi_kabul_edilir_ve_normalize_edilir(client):
    payload = _case_payload()
    payload["target"] = {
        "first_name": "  Kerem  ",
        "last_name": "Boz",
        "hints": {"school": "  ODTÜ  ", "city": "Ankara"},
    }
    payload["seeds"] = [
        {"platform": "github", "username": "kerem.boz"},
        {"platform": "web", "email": "kerem@example.com"},
    ]
    res = await client.post("/api/v1/cases", json=payload)
    assert res.status_code == 201, res.text

    detail = await client.get(f"/api/v1/cases/{res.json()['id']}")
    body = detail.json()
    assert body["target"]["first_name"] == "Kerem"
    assert body["target"]["hints"]["school"] == "ODTÜ"
    assert len(body["seeds"]) == 2

"""T-108 guvenlik sertlestirme testleri: kimlik dogrulama + CORS + iptal ucu.

Kapsam:
* (a) token yok/yanlis -> 401 (+ WWW-Authenticate), dogru token -> 200,
  `/health` tokensuz 200,
* (b) preflight: zararli origin -> `Access-Control-Allow-Origin` yok,
  izinli origin -> var,
* iptal ucu `ScannerService` iptal kanalini tetikler.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.session import get_db
from showis.main import app
from showis.services.scanner import ScannerService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

VALID_TOKEN = "unit-test-token-value"


@pytest.fixture
async def api_db():
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
def auth_enabled(monkeypatch):
    """Auth'u gercekten devreye alir (autouse bypass fixture'ini ezer)."""
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SHOWIS_API_TOKEN", VALID_TOKEN)
    return VALID_TOKEN


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# (a) Kimlik dogrulama
# ---------------------------------------------------------------------------


async def test_token_yoksa_401(client, api_db, auth_enabled):
    res = await client.get("/api/v1/cases")
    assert res.status_code == 401
    assert res.headers.get("www-authenticate") == "Bearer"


async def test_dogru_token_200(client, api_db, auth_enabled):
    res = await client.get("/api/v1/cases", headers={"X-API-Token": auth_enabled})
    assert res.status_code == 200

    # Bearer formu da kabul edilir
    res = await client.get(
        "/api/v1/cases", headers={"Authorization": f"Bearer {auth_enabled}"}
    )
    assert res.status_code == 200


async def test_yanlis_token_401(client, api_db, auth_enabled):
    res = await client.get("/api/v1/cases", headers={"X-API-Token": "yanlis-token"})
    assert res.status_code == 401

    res = await client.get(
        "/api/v1/cases", headers={"Authorization": "Bearer yanlis-token"}
    )
    assert res.status_code == 401


async def test_health_tokensuz_200(client, auth_enabled):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


async def test_health_token_tanimsizken_de_calisir(client, monkeypatch):
    """T-114: healthcheck auth yapilandirmasindan bagimsiz kalmali."""
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("SHOWIS_API_TOKEN", raising=False)
    health = await client.get("/health")
    assert health.status_code == 200
    # Diger uclar fail-closed
    assert (await client.get("/api/v1/cases")).status_code == 503


async def test_token_tanimli_degilse_fail_closed(client, api_db, monkeypatch):
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("SHOWIS_API_TOKEN", raising=False)
    res = await client.get("/api/v1/cases")
    assert res.status_code == 503


# ---------------------------------------------------------------------------
# (b) CORS
# ---------------------------------------------------------------------------


async def test_preflight_zararli_origin_reddedilir(client):
    res = await client.options(
        "/api/v1/cases",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Token",
        },
    )
    assert "access-control-allow-origin" not in res.headers
    assert "access-control-allow-credentials" not in res.headers


async def test_preflight_izinli_origin_kabul_edilir(client):
    res = await client.options(
        "/api/v1/cases",
        headers={
            "Origin": "http://localhost:8005",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Token",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:8005"
    assert res.headers.get("access-control-allow-credentials") is None


# ---------------------------------------------------------------------------
# Iptal ucu -> surec ici kanal
# ---------------------------------------------------------------------------


async def test_cancel_ucu_iptal_kanalini_tetikler(client, api_db):
    scan_id = "scan-cancel-endpoint"
    async with api_db() as session:
        session.add(
            DbCase(
                id="case-cancel",
                name="Cancel",
                purpose="test",
                legal_basis="self_check",
            )
        )
        session.add(DbScan(id=scan_id, case_id="case-cancel", mode="O2", status="running"))
        await session.commit()

    try:
        assert ScannerService.is_cancelled(scan_id) is False
        res = await client.post(f"/api/v1/scans/{scan_id}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"
        # Calisan donguye sinyal gercekten ulasti
        assert ScannerService.is_cancelled(scan_id) is True

        async with api_db() as session:
            status = (
                await session.execute(select(DbScan.status).where(DbScan.id == scan_id))
            ).scalar_one()
        assert status == "cancelled"

        # Idempotent: ikinci cagri yeni audit uretmez, 200 doner
        res = await client.post(f"/api/v1/scans/{scan_id}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"
    finally:
        ScannerService.clear_cancel(scan_id)


# ---------------------------------------------------------------------------
# (c) T-114: dokumantasyon varsayilan kapali, acikken auth arkasinda
# ---------------------------------------------------------------------------


async def test_docs_kapaliyken_404(client, api_db, monkeypatch):
    monkeypatch.delenv("SHOWIS_DOCS_ENABLED", raising=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        res = await client.get(path)
        assert res.status_code == 404, f"{path} -> {res.status_code}"


async def test_docs_acikken_token_ister(client, api_db, auth_enabled, monkeypatch):
    monkeypatch.setenv("SHOWIS_DOCS_ENABLED", "1")
    res = await client.get("/openapi.json")
    assert res.status_code == 401
    assert res.headers.get("www-authenticate") == "Bearer"

    res = await client.get("/openapi.json", headers={"X-API-Token": auth_enabled})
    assert res.status_code == 200
    body = res.json()
    assert body["info"]["title"] == "Showis API"
    assert "/api/v1/cases" in body["paths"]


async def test_docs_acikken_bypass_ile_200(client, api_db, monkeypatch):
    """Dev/test (SHOWIS_AUTH_DISABLED=1) bypass'i dokumanlarda da gecerli."""
    monkeypatch.setenv("SHOWIS_DOCS_ENABLED", "true")
    assert (await client.get("/openapi.json")).status_code == 200
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/redoc")).status_code == 200


async def test_docs_kapaliyken_token_olsa_da_404(client, api_db, auth_enabled, monkeypatch):
    """Kapali dokumantasyon token'la da acilmaz (404 once gelir)."""
    monkeypatch.delenv("SHOWIS_DOCS_ENABLED", raising=False)
    res = await client.get("/openapi.json", headers={"X-API-Token": auth_enabled})
    assert res.status_code == 404

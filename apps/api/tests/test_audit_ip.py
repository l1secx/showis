"""T-320 (D-01): `actor_ip` güven modeli — X-Real-IP > XFF son > client.host."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import AuditLog
from showis.db.models import Case as DbCase
from showis.db.models import Entity as DbEntity
from showis.db.models import Finding as DbFinding
from showis.db.models import Scan as DbScan
from showis.db.session import get_db
from showis.main import app
from showis.services.audit import client_ip_from_headers
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
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Birim testleri: başlık çözümleme
# ---------------------------------------------------------------------------


def test_xff_ilk_degere_guvenilmez_son_deger_alinir():
    # Saldırgan başa 1.2.3.4 yazmış olsa da Caddy'nin eklediği son atlama kazanır
    assert (
        client_ip_from_headers({"x-forwarded-for": "1.2.3.4, 10.0.0.1"}, "127.0.0.1")
        == "10.0.0.1"
    )


def test_x_real_ip_xff_den_once_gelir():
    headers = {"x-real-ip": "198.51.100.7", "x-forwarded-for": "1.2.3.4, 10.0.0.1"}
    assert client_ip_from_headers(headers, "127.0.0.1") == "198.51.100.7"


def test_baslik_yoksa_client_host():
    assert client_ip_from_headers({}, "127.0.0.1") == "127.0.0.1"
    assert client_ip_from_headers({}, None) is None


def test_gecersiz_baslik_degerleri_reddedilir():
    # Boşluk/CRLF/enjeksiyon denemeleri kabul edilmez, alt kaynağa düşülür
    assert client_ip_from_headers({"x-real-ip": "  "}, "10.1.1.1") == "10.1.1.1"
    assert (
        client_ip_from_headers({"x-real-ip": "evil\r\nX-Injected: 1"}, "10.1.1.1")
        == "10.1.1.1"
    )
    assert client_ip_from_headers({"x-real-ip": "a" * 100}, "10.1.1.1") == "10.1.1.1"
    # IPv6 kabul edilir
    assert client_ip_from_headers({"x-real-ip": "2001:db8::1"}, None) == "2001:db8::1"


# ---------------------------------------------------------------------------
# Uçtan uca: audit kaydı hangi IP'yi taşır
# ---------------------------------------------------------------------------


async def _seed_finding(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        session.add(
            DbCase(
                id="case-ip",
                name="IP Testi",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(DbScan(id="scan-ip", case_id="case-ip", mode="O2", status="completed"))
        session.add(
            DbEntity(
                id="github:ipuser",
                case_id="case-ip",
                type="account",
                platform="github",
                label="ipuser",
                attributes={"username": "ipuser"},
            )
        )
        session.add(
            DbFinding(
                id="finding:github:ipuser",
                case_id="case-ip",
                scan_id="scan-ip",
                entity_id="github:ipuser",
                platform="github",
                label="ipuser",
                status="candidate",
                score=0.7,
                breakdown={},
            )
        )
        await session.commit()


async def _audit_ip(factory: async_sessionmaker[AsyncSession]) -> str | None:
    async with factory() as session:
        entry = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "finding.update")
            )
        ).scalars().first()
    assert entry is not None
    return (entry.details or {}).get("actor_ip")


async def test_audit_xff_son_degerini_yazar(client, db_factory):
    await _seed_finding(db_factory)
    res = await client.patch(
        "/api/v1/findings/finding:github:ipuser",
        json={"status": "confirmed"},
        headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.1"},
    )
    assert res.status_code == 200, res.text
    assert await _audit_ip(db_factory) == "10.0.0.1"


async def test_audit_x_real_ip_kazanir(client, db_factory):
    await _seed_finding(db_factory)
    res = await client.patch(
        "/api/v1/findings/finding:github:ipuser",
        json={"status": "rejected"},
        headers={
            "X-Real-IP": "198.51.100.7",
            "X-Forwarded-For": "1.2.3.4, 10.0.0.1",
        },
    )
    assert res.status_code == 200, res.text
    assert await _audit_ip(db_factory) == "198.51.100.7"


async def test_audit_baslik_yokken_client_host_yazar(client, db_factory):
    await _seed_finding(db_factory)
    res = await client.patch(
        "/api/v1/findings/finding:github:ipuser", json={"status": "candidate"}
    )
    assert res.status_code == 200, res.text
    # ASGITransport istemcisinin varsayılan host'u
    assert await _audit_ip(db_factory) == "127.0.0.1"


async def test_auth_failed_audit_son_xff_degerini_yazar(client, db_factory, monkeypatch):
    import showis.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "async_session_factory", db_factory)
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SHOWIS_API_TOKEN", "dogru-token")

    res = await client.get(
        "/api/v1/cases",
        headers={"X-API-Token": "yanlis", "X-Forwarded-For": "1.2.3.4, 10.0.0.1"},
    )
    assert res.status_code == 401

    async with db_factory() as session:
        entry = (
            await session.execute(select(AuditLog).where(AuditLog.action == "auth.failed"))
        ).scalars().first()
    assert entry is not None
    assert entry.details["actor_ip"] == "10.0.0.1"
    assert entry.details["status_code"] == 401

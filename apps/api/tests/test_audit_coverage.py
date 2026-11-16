"""T-302: audit kapsamı (SEC-13) + SSE sertleştirme (SEC-16) testleri."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from showis.api.v1 import scans as scans_api
from showis.db.base import Base
from showis.db.models import AuditLog
from showis.db.models import Case as DbCase
from showis.db.models import Entity as DbEntity
from showis.db.models import Finding as DbFinding
from showis.db.models import Report as DbReport
from showis.db.models import Scan as DbScan
from showis.db.session import get_db
from showis.main import app
from showis.services.orchestrator import event_broadcaster
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

FORWARDED_IP = "203.0.113.9"


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
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Forwarded-For": FORWARDED_IP}
    ) as c:
        yield c


async def _seed_case_scan(
    factory: async_sessionmaker[AsyncSession],
    *,
    case_id: str = "case-audit",
    scan_id: str = "scan-audit",
    scan_status: str = "running",
) -> None:
    async with factory() as session:
        session.add(
            DbCase(
                id=case_id,
                name="Audit Case",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(DbScan(id=scan_id, case_id=case_id, mode="O2", status=scan_status))
        await session.commit()


async def _actions(
    factory: async_sessionmaker[AsyncSession], case_id: str | None = None
) -> list[AuditLog]:
    async with factory() as session:
        stmt = select(AuditLog).order_by(AuditLog.id)
        if case_id:
            stmt = stmt.where(AuditLog.case_id == case_id)
        return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# A. Audit kapsamı (SEC-13)
# ---------------------------------------------------------------------------


async def test_finding_update_audit_ip_ile_yazilir(client, db_factory):
    await _seed_case_scan(db_factory)
    async with db_factory() as session:
        session.add(
            DbEntity(
                id="github:audit-user",
                case_id="case-audit",
                type="account",
                platform="github",
                label="audit-user",
                attributes={"username": "audit-user"},
            )
        )
        session.add(
            DbFinding(
                id="finding:github:audit-user",
                case_id="case-audit",
                scan_id="scan-audit",
                entity_id="github:audit-user",
                platform="github",
                label="audit-user",
                status="candidate",
                score=0.8,
                breakdown={},
            )
        )
        await session.commit()

    res = await client.patch(
        "/api/v1/findings/finding:github:audit-user", json={"status": "confirmed"}
    )
    assert res.status_code == 200, res.text

    rows = await _actions(db_factory, "case-audit")
    entry = next(r for r in rows if r.action == "finding.update")
    assert entry.details["from"] == "candidate"
    assert entry.details["to"] == "confirmed"
    assert entry.details["actor_ip"] == FORWARDED_IP
    assert entry.actor == "operator"
    assert entry.target == "Finding: finding:github:audit-user"


async def test_report_download_audit_yazilir(client, db_factory, tmp_path, monkeypatch):
    import showis.services.report_service as report_service

    # T-316: indirme yalnizca REPORT_DIR icindeki dosyalari servis eder
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    monkeypatch.setattr(report_service, "REPORT_DIR", reports_dir)
    await _seed_case_scan(db_factory)
    report_path = reports_dir / "report.json"
    report_path.write_text('{"case":"case-audit"}', encoding="utf-8")
    async with db_factory() as session:
        session.add(
            DbReport(
                id="report-1",
                case_id="case-audit",
                format="json",
                storage_key=str(report_path),
            )
        )
        await session.commit()

    res = await client.get("/api/v1/reports/report-1/download")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/json")

    rows = await _actions(db_factory, "case-audit")
    entry = next(r for r in rows if r.action == "report.download")
    assert entry.details["format"] == "json"
    assert entry.details["actor_ip"] == FORWARDED_IP


async def test_case_delete_audit_ip_tasir(client, db_factory):
    await _seed_case_scan(db_factory)
    res = await client.delete("/api/v1/cases/case-audit")
    assert res.status_code == 204, res.text

    rows = await _actions(db_factory)
    entry = next(r for r in rows if r.action == "case.delete")
    assert entry.details["actor_ip"] == FORWARDED_IP


async def test_scan_cancel_audit_ip_tasir(client, db_factory):
    await _seed_case_scan(db_factory, case_id="case-cancel-audit", scan_id="scan-cancel-audit")
    res = await client.post("/api/v1/scans/scan-cancel-audit/cancel")
    assert res.status_code == 200, res.text

    rows = await _actions(db_factory, "case-cancel-audit")
    entry = next(r for r in rows if r.action == "scan.cancel")
    assert entry.details["actor_ip"] == FORWARDED_IP


async def test_auth_failed_audit_yazilir(client, db_factory, monkeypatch):
    """401 ureten istek `auth.failed` olarak audit'e duser; token loglanmaz."""
    import showis.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "async_session_factory", db_factory)
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SHOWIS_API_TOKEN", "dogru-token")

    res = await client.get("/api/v1/cases", headers={"X-API-Token": "yanlis-token"})
    assert res.status_code == 401

    rows = await _actions(db_factory)
    entry = next(r for r in rows if r.action == "auth.failed")
    assert entry.details["status_code"] == 401
    assert entry.details["actor_ip"] == FORWARDED_IP
    assert entry.target == "GET /api/v1/cases"
    # Sır değeri audit'e sızmaz
    assert "yanlis-token" not in str(entry.details)
    assert "dogru-token" not in str(entry.details)


# ---------------------------------------------------------------------------
# B. SSE (SEC-16)
# ---------------------------------------------------------------------------


async def test_sse_bilinmeyen_scan_404(client, db_factory):
    res = await client.get("/api/v1/scans/yok-boyle-scan/events")
    assert res.status_code == 404


async def test_sse_bitmis_taramada_hemen_kapanir(client, db_factory):
    await _seed_case_scan(
        db_factory, case_id="case-sse-done", scan_id="scan-sse-done", scan_status="completed"
    )
    chunks: list[str] = []
    async with client.stream("GET", "/api/v1/scans/scan-sse-done/events") as response:
        assert response.status_code == 200
        async for chunk in response.aiter_text():
            chunks.append(chunk)

    body = "".join(chunks)
    assert "event: scan.completed" in body
    # Akis kapandi (sonsuz bekleme yok)
    assert scans_api._sse_connections.get("scan-sse-done") is None


async def test_sse_baglanti_siniri_429(client, db_factory, monkeypatch):
    """Ayni tarama icin 3 acik baglanti varken yeni baglanti 429 alir.

    Not: `httpx.ASGITransport` akisi sonuna kadar bekledigi icin gercek
    "acik baglanti" simulasyonu yapilamaz; sayac dogrudan doldurulur — uctaki
    kontrol ayni kod yolunu kullanir. Sinir altindaki durum, akis omru
    kisaltilarak dogrulanir.
    """
    await _seed_case_scan(
        db_factory, case_id="case-sse-limit", scan_id="scan-sse-limit", scan_status="running"
    )
    # T-354: slot tablosu artik kira (lease) tabanli; sayac kiradan turetilir
    leases = [
        scans_api.acquire_sse_lease("scan-sse-limit")
        for _ in range(scans_api._MAX_SSE_CONNECTIONS)
    ]
    try:
        res = await client.get("/api/v1/scans/scan-sse-limit/events")
        assert res.status_code == 429
    finally:
        for lease in leases:
            scans_api.release_sse_lease(lease)

    # Sinir altinda baglanti kurulur ve akis normal sekilde sonlanir
    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.05")
    async with client.stream("GET", "/api/v1/scans/scan-sse-limit/events") as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])
    assert ": connected" in body
    assert ": bye" in body
    assert scans_api._sse_connections.get("scan-sse-limit") is None


async def test_sse_sure_asiminda_bye_ile_kapanir(client, db_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.1")
    await _seed_case_scan(
        db_factory, case_id="case-sse-timeout", scan_id="scan-sse-timeout", scan_status="running"
    )
    chunks: list[str] = []
    async with client.stream("GET", "/api/v1/scans/scan-sse-timeout/events") as response:
        assert response.status_code == 200
        async for chunk in response.aiter_text():
            chunks.append(chunk)

    assert ": bye" in "".join(chunks)
    assert scans_api._sse_connections.get("scan-sse-timeout") is None


async def test_sse_olay_akisi_terminal_olayla_kapanir(client, db_factory):
    """Acik baglanti terminal olay geldiginde kapanir (event kaybi yok)."""
    await _seed_case_scan(
        db_factory, case_id="case-sse-live", scan_id="scan-sse-live", scan_status="running"
    )
    received: list[str] = []

    async def _publish() -> None:
        await asyncio.sleep(0.05)
        await event_broadcaster.broadcast("scan-sse-live", "log", {"message": "merhaba"})
        await event_broadcaster.broadcast(
            "scan-sse-live", "scan.completed", {"status": "completed"}
        )

    publisher = asyncio.create_task(_publish())
    try:
        async with client.stream("GET", "/api/v1/scans/scan-sse-live/events") as response:
            async for chunk in response.aiter_text():
                received.append(chunk)
    finally:
        await publisher

    body = "".join(received)
    assert "event: log" in body
    assert "event: scan.completed" in body
    assert scans_api._sse_connections.get("scan-sse-live") is None

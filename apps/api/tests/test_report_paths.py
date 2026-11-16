"""T-316: rapor yolları (vaka klasörü + legacy), HTML biçimi, SSE kuyruk sınırı."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from showis.api.v1 import scans as scans_api
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Report as DbReport
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.main import app
from showis.services import report_service
from showis.services.orchestrator import ScanEventBroadcaster, ScanEventQueue, event_broadcaster
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(report_service, "REPORT_DIR", reports_dir)
    monkeypatch.setenv("SHOWIS_REPORT_DIR", str(reports_dir))
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


async def _seed_case(factory: async_sessionmaker[AsyncSession], case_id: str = "case-t316") -> str:
    async with factory() as session:
        session.add(
            DbCase(
                id=case_id,
                name="Rapor Testi",
                purpose="test",
                legal_basis="self_check",
                retention_days=30,
            )
        )
        session.add(DbTarget(case_id=case_id, first_name="Ada", last_name="Lovelace", hints={}))
        await session.commit()
    return case_id


# ---------------------------------------------------------------------------
# 1. Vaka bazlı klasör + legacy indirme
# ---------------------------------------------------------------------------


def test_report_path_vaka_klasorunde(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service, "REPORT_DIR", tmp_path / "reports")
    path = report_service._report_path("case-abc", "pdf")

    assert path.parent == (tmp_path / "reports" / "case-abc").resolve()
    assert path.suffix == ".pdf"
    assert len(path.stem) == 8


def test_report_path_traversal_engellenir(tmp_path, monkeypatch):
    monkeypatch.setattr(report_service, "REPORT_DIR", tmp_path / "reports")
    path = report_service._report_path("../../etc/passwd", "json")
    assert (tmp_path / "reports").resolve() in path.parents
    assert path.parent.name == "etcpasswd"


async def test_create_report_klasor_olusturur_ve_storage_key_icerde(client, db_factory, tmp_path):
    case_id = await _seed_case(db_factory)
    res = await client.post(f"/api/v1/cases/{case_id}/report", json={"format": "json"})
    assert res.status_code == 200, res.text
    report_id = res.json()["id"]

    async with db_factory() as session:
        report = (
            await session.execute(select(DbReport).where(DbReport.id == report_id))
        ).scalar_one()
    stored = report_service.Path(report.storage_key)
    base = (tmp_path / "reports").resolve()
    assert stored.resolve().is_relative_to(base / report_service.safe_case_token(case_id))
    assert stored.exists()
    assert stored.parent.is_dir()

    # İndirme çalışır
    dl = await client.get(f"/api/v1/reports/{report_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/json")


async def test_eski_duz_yerlesim_hala_indirilebilir(client, db_factory, tmp_path):
    """T112-R3 öncesi düz dosyalar (`REPORT_DIR/<ad>`) okunmaya devam eder."""
    case_id = await _seed_case(db_factory, "case-legacy")
    base = tmp_path / "reports"
    base.mkdir(parents=True, exist_ok=True)
    legacy = base / "eski-rapor.json"
    legacy.write_text('{"legacy": true}', encoding="utf-8")

    async with db_factory() as session:
        session.add(
            DbReport(
                id="report-legacy",
                case_id=case_id,
                format="json",
                # Kapsayıcı içi eski mutlak yol: diskte yok -> geriye uyum devreye girer
                storage_key="/app/data/reports/eski-rapor.json",
            )
        )
        await session.commit()

    res = await client.get("/api/v1/reports/report-legacy/download")
    assert res.status_code == 200
    assert res.json() == {"legacy": True}


async def test_dosya_yoksa_404(client, db_factory):
    case_id = await _seed_case(db_factory, "case-missing")
    async with db_factory() as session:
        session.add(
            DbReport(
                id="report-missing",
                case_id=case_id,
                format="json",
                storage_key="/yok/burada.json",
            )
        )
        await session.commit()

    res = await client.get("/api/v1/reports/report-missing/download")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 2. Case silme: klasör + dosyalar temizlenir
# ---------------------------------------------------------------------------


async def test_case_silme_rapor_klasorunu_temizler(client, db_factory, tmp_path):
    case_id = await _seed_case(db_factory, "case-cleanup")
    created = await client.post(f"/api/v1/cases/{case_id}/report", json={"format": "json"})
    assert created.status_code == 200

    folder = (tmp_path / "reports" / report_service.safe_case_token(case_id)).resolve()
    assert folder.is_dir() and list(folder.iterdir())

    res = await client.delete(f"/api/v1/cases/{case_id}")
    assert res.status_code == 204, res.text
    assert not folder.exists(), "vaka rapor klasörü silinmeli"


# ---------------------------------------------------------------------------
# 3. HTML biçimi: içerik tipi + CSP
# ---------------------------------------------------------------------------


async def test_html_rapor_uretimi_ve_csp(client, db_factory, tmp_path):
    case_id = await _seed_case(db_factory, "case-html")
    res = await client.post(f"/api/v1/cases/{case_id}/report", json={"format": "html"})
    assert res.status_code == 200, res.text
    assert res.json()["format"] == "html"

    dl = await client.get(f"/api/v1/reports/{res.json()['id']}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("text/html; charset=utf-8")
    assert dl.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'"
    )
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in dl.headers["content-disposition"]
    body = dl.text
    assert "<!DOCTYPE html>" in body
    assert "Yönetici Özeti" in body


async def test_bilinmeyen_format_422(client, db_factory):
    case_id = await _seed_case(db_factory, "case-badformat")
    res = await client.post(f"/api/v1/cases/{case_id}/report", json={"format": "docx"})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# 4. SSE kuyruk sınırı (SEC-16)
# ---------------------------------------------------------------------------


async def test_kuyruk_tasmasi_en_yeni_olaylari_tutar():
    broadcaster = ScanEventBroadcaster(maxsize=2)
    queue = broadcaster.subscribe("scan-q")

    for index in range(5):
        await broadcaster.broadcast("scan-q", f"event.{index}", {"i": index})

    assert queue.maxsize == 2
    assert queue.qsize() == 2
    types = [queue.get_nowait()["type"] for _ in range(2)]
    assert types == ["event.3", "event.4"]  # en yeni 2
    assert queue.dropped == 3
    assert broadcaster.dropped_events("scan-q") == 3


async def test_kuyruk_varsayilan_kapasitesi_env_ile_ayarlanir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SSE_QUEUE_MAXSIZE", "7")
    broadcaster = ScanEventBroadcaster()
    queue = broadcaster.subscribe("scan-env")
    assert isinstance(queue, ScanEventQueue)
    assert queue.maxsize == 7


async def test_unsubscribe_kuyrugu_ve_sayaci_temizler():
    broadcaster = ScanEventBroadcaster(maxsize=2)
    queue = broadcaster.subscribe("scan-unsub")
    await broadcaster.broadcast("scan-unsub", "log", {"m": "x"})
    assert broadcaster.subscriber_count("scan-unsub") == 1

    broadcaster.unsubscribe("scan-unsub", queue)
    assert broadcaster.subscriber_count("scan-unsub") == 0
    assert broadcaster.dropped_events("scan-unsub") == 0
    assert "scan-unsub" not in broadcaster._queues


async def test_sse_akisi_kapandiğinda_kuyruk_temizlenir(db_factory, client, monkeypatch):
    """Akış bitince (terminal olay) abone kaydı ve sayaç düşer."""
    case_id = await _seed_case(db_factory, "case-sse-q")
    async with db_factory() as session:
        from showis.db.models import Scan as DbScan

        session.add(DbScan(id="scan-sse-q", case_id=case_id, mode="O2", status="running"))
        await session.commit()

    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.05")
    assert event_broadcaster.subscriber_count("scan-sse-q") == 0
    async with client.stream("GET", "/api/v1/scans/scan-sse-q/events") as response:
        async for _chunk in response.aiter_text():
            pass
    assert event_broadcaster.subscriber_count("scan-sse-q") == 0
    assert scans_api._sse_connections.get("scan-sse-q") is None


async def test_broadcast_abone_yokken_calisir():
    broadcaster = ScanEventBroadcaster(maxsize=1)
    await broadcaster.broadcast("yok-boyle-scan", "log", {"m": "x"})
    assert broadcaster.subscriber_count("yok-boyle-scan") == 0
    await asyncio.sleep(0)

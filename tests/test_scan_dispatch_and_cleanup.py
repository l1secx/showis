"""T-112: Celery dispatch kaydı, IntegrityError→409, rapor dosyası temizliği."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import AuditLog, Report, Scan
from showis.db.session import get_db
from showis.main import app
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

#: 0003_active_scan_unique ile ayni kismi index (sqlite 3.8+ destekler)
_ACTIVE_INDEX_SQL = (
    "CREATE UNIQUE INDEX uq_scans_case_active ON scans(case_id) "
    "WHERE status IN ('queued', 'running')"
)


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(_ACTIVE_INDEX_SQL))
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
def _isolated_dirs(tmp_path, monkeypatch):
    """Rapor/kanıt dizinleri her testte geçici (host'taki data/ kirletilmez)."""
    monkeypatch.setenv("SHOWIS_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("SHOWIS_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.delenv("USE_CELERY", raising=False)

    # Tarama arka planda gerçek DB'ye bağlanmasın (bu testler API sözleşmesini ölçer)
    async def _noop_scan(scan_id: str) -> None:
        return None

    monkeypatch.setattr("showis.api.v1.cases._run_scan_inline", _noop_scan)
    monkeypatch.setattr("showis.api.v1.cases.run_scan_in_background", _noop_scan)
    yield


async def _create_case(client, name: str = "T112 Vaka") -> str:
    res = await client.post(
        "/api/v1/cases",
        json={
            "name": name,
            "purpose": "kendi hesabimi denetlemek icin",
            "legal_basis": "self_check",
            "target": {"first_name": "Kerem", "last_name": "Boz", "hints": {}},
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ---------------------------------------------------------------------------
# 1) Celery task id -> scan.stats
# ---------------------------------------------------------------------------


class _FakeAsyncResult:
    def __init__(self, task_id: str):
        self.id = task_id


@pytest.mark.asyncio
async def test_celery_task_id_scan_statsa_yazilir(
    client, session_factory, monkeypatch
):
    monkeypatch.setenv("USE_CELERY", "1")
    calls: list[dict] = []

    def _fake_send_task(name, args=None, queue=None, **kwargs):
        calls.append({"name": name, "args": args, "queue": queue})
        return _FakeAsyncResult("celery-task-abc123")

    import showis.services.worker as worker

    monkeypatch.setattr(worker.celery_app, "send_task", _fake_send_task)

    case_id = await _create_case(client)
    res = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    assert res.status_code == 200, res.text
    scan_id = res.json()["scan_id"]

    async with session_factory() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == scan_id))
        ).scalar_one()
        logs = (await session.execute(select(AuditLog))).scalars().all()

    assert scan.stats["celery_task_id"] == "celery-task-abc123"
    assert calls and calls[0]["name"] == "showis.scan.run"
    assert calls[0]["args"] == [scan_id]
    assert calls[0]["queue"] == "osint"
    start_logs = [log for log in logs if log.action == "scan.start"]
    assert start_logs[0].details["celery_task_id"] == "celery-task-abc123"


@pytest.mark.asyncio
async def test_celery_yoksa_task_id_yazilmaz(client, session_factory):
    case_id = await _create_case(client)
    res = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O1"})
    scan_id = res.json()["scan_id"]
    async with session_factory() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == scan_id))
        ).scalar_one()
    assert "celery_task_id" not in (scan.stats or {})


@pytest.mark.asyncio
async def test_celery_dispatch_hatasi_taramayi_bloklamaz(
    client, session_factory, monkeypatch
):
    monkeypatch.setenv("USE_CELERY", "1")

    def _boom(*args, **kwargs):
        raise ConnectionError("broker yok")

    import showis.services.worker as worker

    monkeypatch.setattr(worker.celery_app, "send_task", _boom)

    case_id = await _create_case(client)
    res = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    assert res.status_code == 200
    async with session_factory() as session:
        scan = (
            await session.execute(select(Scan).where(Scan.id == res.json()["scan_id"]))
        ).scalar_one()
        logs = (await session.execute(select(AuditLog))).scalars().all()
    assert "celery_task_id" not in (scan.stats or {})
    assert any(
        log.action == "scan.start" and log.details["celery_dispatch_failed"]
        for log in logs
    )


# ---------------------------------------------------------------------------
# 2) DB kısmi unique index -> IntegrityError -> 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yaris_kaybedince_integrityerror_409a_cevrilir(
    client, session_factory, monkeypatch
):
    case_id = await _create_case(client)

    # Rakip istek ön kontrolü geçmiş gibi davran (yarış simülasyonu).
    async def _no_active(db, case_id_arg):
        return None

    monkeypatch.setattr("showis.api.v1.cases._find_active_scan", _no_active)

    # Rakibin yazdığı satır: commit anında unique index ihlali üretecek.
    async with session_factory() as session:
        session.add(
            Scan(case_id=case_id, status="queued", mode="O2", budget={}, stats={})
        )
        await session.commit()

    res = await client.post(f"/api/v1/cases/{case_id}/scan", json={"mode": "O2"})
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["error"] == "scan_already_active"

    # Ek satır yazılmamış olmalı (rollback)
    async with session_factory() as session:
        scans = (
            await session.execute(select(Scan).where(Scan.case_id == case_id))
        ).scalars().all()
    assert len(scans) == 1


def test_kismi_index_aktif_iki_taramayi_engeller(session_factory):
    """Index'in kendisi doğru tanımlı mı? (migration 0003 eşdeğeri)"""

    async def _run():
        async with session_factory() as session:
            session.add(Scan(case_id="c-x", status="queued", mode="O2", budget={}))
            await session.commit()
            session.add(Scan(case_id="c-x", status="running", mode="O2", budget={}))
            with pytest.raises(IntegrityError):
                await session.commit()

    import asyncio

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4) Rapor dosyaları kasa silinince diskten kalkar (SEC-12)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_case_silme_rapor_dosyalarini_diskten_siler(
    client, session_factory, tmp_path
):
    case_id = await _create_case(client)
    report_dir = Path(os.environ["SHOWIS_REPORT_DIR"])
    report_dir.mkdir(parents=True, exist_ok=True)

    # 1) Adında case_id geçen yetim dosya (storage_key satırı olmayan)
    orphan = report_dir / f"{case_id}_deadbeef.pdf"
    orphan.write_bytes(b"%PDF-1.4 test")
    # 2) DB'de kayıtlı rapor (storage_key)
    stored_dir = tmp_path / "stored"
    stored_dir.mkdir()
    stored = stored_dir / "rapor.json"
    stored.write_text("{}", encoding="utf-8")
    async with session_factory() as session:
        session.add(Report(case_id=case_id, format="json", storage_key=str(stored)))
        await session.commit()

    # alakasız dosya korunmalı
    other = report_dir / "baska-vaka.pdf"
    other.write_bytes(b"keep")

    res = await client.delete(f"/api/v1/cases/{case_id}")
    assert res.status_code == 204, res.text

    assert not orphan.exists(), "case_id içeren rapor dosyası silinmeli"
    assert not stored.exists(), "reports.storage_key dosyası silinmeli"
    assert other.exists(), "başka vakanın dosyasına dokunulmamalı"


@pytest.mark.asyncio
async def test_rapor_silme_basarisizsa_audit_yazilir(
    client, session_factory, tmp_path
):
    case_id = await _create_case(client)
    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    target = locked_dir / "rapor.pdf"
    target.write_bytes(b"x")
    async with session_factory() as session:
        session.add(Report(case_id=case_id, format="pdf", storage_key=str(target)))
        await session.commit()

    # Yazma izni kaldır: unlink EACCES ile başarısız olur
    locked_dir.chmod(0o500)
    try:
        res = await client.delete(f"/api/v1/cases/{case_id}")
        assert res.status_code == 204, res.text
        async with session_factory() as session:
            logs = (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == "report.cleanup_failed")
                )
            ).scalars().all()
        assert logs, "silinemeyen rapor için audit kaydı bekleniyor"
        assert logs[0].details["failed_count"] >= 1
    finally:
        locked_dir.chmod(0o700)

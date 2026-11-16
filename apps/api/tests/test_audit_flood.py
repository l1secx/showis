"""T-343: 401/413 audit hız sınırı (T-340 bulgu #4) testleri.

Değişmezler:
* aynı (peer|claimed, action, status) için dakikada en fazla N kayıt,
* fazlası bastırılır; pencere dolunca **tek** "suppressed: N" özeti yazılır,
* XFF uydurarak atlatılamaz (peer IP anahtarda),
* 503 (token yapılandırılmamış) dakikada 1,
* mutlu yol hiç etkilenmez.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from showis.db.base import Base
from showis.db.models import AuditLog
from showis.db.session import get_db
from showis.main import app
from showis.services import audit as audit_module
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TOKEN = "t343-token"


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


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, db_factory):
    """Her test temiz sayaç + sabit saat + test DB'si ile başlar."""
    import showis.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "async_session_factory", db_factory)
    audit_module.reset_audit_throttle()
    monkeypatch.setenv("SHOWIS_AUTH_AUDIT_PER_MINUTE", "5")
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("SHOWIS_API_TOKEN", TOKEN)
    clock = {"now": 1000.0}
    monkeypatch.setattr(audit_module, "_now", lambda: clock["now"])
    yield clock
    audit_module.reset_audit_throttle()


async def _audit_rows(factory, action: str) -> list[AuditLog]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )


def _ip_headers(claimed: str = "198.51.100.1") -> dict[str, str]:
    return {"X-API-Token": "yanlis-token", "X-Real-IP": claimed}


# ---------------------------------------------------------------------------
# (a) 20 hızlı 401 → en fazla 5 kayıt + 1 özet
# ---------------------------------------------------------------------------


async def test_yirmi_hizli_401_bes_kayit_ve_ozet(client, db_factory, reset_state):
    for _ in range(20):
        res = await client.get("/api/v1/cases", headers=_ip_headers())
        assert res.status_code == 401

    rows = await _audit_rows(db_factory, "auth.failed")
    assert len(rows) == 5, f"beklenen 5 kayıt, {len(rows)} yazıldı"
    assert all("suppressed" not in (row.details or {}) for row in rows)

    # Pencere dolduktan sonraki ilk olay: bastırılanların özeti + yeni kayıt
    reset_state["now"] += 61
    res = await client.get("/api/v1/cases", headers=_ip_headers())
    assert res.status_code == 401

    rows = await _audit_rows(db_factory, "auth.failed")
    assert len(rows) == 7  # 5 + özet + 1 yeni
    summaries = [row for row in rows if (row.details or {}).get("summary")]
    assert len(summaries) == 1
    assert summaries[0].details["suppressed"] == 15
    assert summaries[0].details["window_seconds"] == 60


async def test_env_ile_limit_degisir(client, db_factory, reset_state, monkeypatch):
    monkeypatch.setenv("SHOWIS_AUTH_AUDIT_PER_MINUTE", "2")
    for _ in range(6):
        await client.get("/api/v1/cases", headers=_ip_headers())

    assert len(await _audit_rows(db_factory, "auth.failed")) == 2


# ---------------------------------------------------------------------------
# (b) Farklı IP'ler ayrı sayılır; XFF uydurmak atlatamaz
# ---------------------------------------------------------------------------


async def test_farkli_ipler_ayri_sayilir(client, db_factory, reset_state):
    for claimed in ("198.51.100.1", "198.51.100.2"):
        for _ in range(8):
            await client.get("/api/v1/cases", headers=_ip_headers(claimed))

    rows = await _audit_rows(db_factory, "auth.failed")
    assert len(rows) == 10  # her IP için 5
    ips = {(row.details or {}).get("actor_ip") for row in rows}
    assert ips == {"198.51.100.1", "198.51.100.2"}


async def test_ayni_peer_farkli_xff_bucketlari_sinirli(client, db_factory, reset_state):
    """XFF spoof edilse bile bucket sayısı MAX_BUCKETS ile sınırlı (bellek koruması)."""
    throttle = audit_module.audit_throttle()
    original = throttle.max_buckets
    throttle.max_buckets = 3
    try:
        for index in range(10):
            await client.get(
                "/api/v1/cases", headers=_ip_headers(f"203.0.113.{index}")
            )
    finally:
        throttle.max_buckets = original

    assert len(throttle.buckets) <= 3
    # Her yeni anahtar ilk kaydı yazar; toplam yine de sınırlı büyür (10 olay → ≤10 kayıt)
    assert len(await _audit_rows(db_factory, "auth.failed")) <= 10


# ---------------------------------------------------------------------------
# (c) 60 sn sonra tekrar yazılır
# ---------------------------------------------------------------------------


async def test_pencere_donunce_yeniden_yazilir(client, db_factory, reset_state):
    for _ in range(5):
        await client.get("/api/v1/cases", headers=_ip_headers())
    assert len(await _audit_rows(db_factory, "auth.failed")) == 5

    # Aynı pencere içinde yeni kayıt yok
    await client.get("/api/v1/cases", headers=_ip_headers())
    assert len(await _audit_rows(db_factory, "auth.failed")) == 5

    # 61 sn sonra: tekrar 5 hak
    reset_state["now"] += 61
    for _ in range(5):
        await client.get("/api/v1/cases", headers=_ip_headers())
    rows = await _audit_rows(db_factory, "auth.failed")
    # 5 (ilk pencere) + 1 özet (bastırılan 1) + 5 (yeni pencere)
    assert len(rows) == 11


# ---------------------------------------------------------------------------
# (d) 503 → dakikada 1
# ---------------------------------------------------------------------------


async def test_503_dakikada_bir(client, db_factory, reset_state, monkeypatch):
    monkeypatch.delenv("SHOWIS_API_TOKEN", raising=False)
    for _ in range(6):
        res = await client.get("/api/v1/cases")
        assert res.status_code == 503

    rows = await _audit_rows(db_factory, "auth.failed")
    assert len(rows) == 1

    reset_state["now"] += 61
    res = await client.get("/api/v1/cases")
    assert res.status_code == 503
    rows = await _audit_rows(db_factory, "auth.failed")
    assert len(rows) == 3  # 1 + özet(suppressed=5) + 1


# ---------------------------------------------------------------------------
# (e) Mutlu yol etkilenmez
# ---------------------------------------------------------------------------


async def test_mutlu_yolda_audit_yazilmaz(client, db_factory):
    res = await client.get("/api/v1/cases", headers={"X-API-Token": TOKEN})
    assert res.status_code == 200
    assert await _audit_rows(db_factory, "auth.failed") == []


async def test_health_muaf_audit_uretmez(client, db_factory):
    """/health ve /healthz auth denetimi dışıdır.

    Önemli: `/healthz` derin kontrol DB'ye bağlı olduğu için DB yokken **503**
    döner; bu 503 `auth.failed` olarak yazılırsa container healthcheck'i
    (10 sn'de bir) audit tablosunu doldurur. Test bunu kilitler.
    """
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/healthz")).status_code in (200, 503)
    assert await _audit_rows(db_factory, "auth.failed") == []


# ---------------------------------------------------------------------------
# (f) 413 seli de hız sınırlı
# ---------------------------------------------------------------------------


async def test_413_seli_hiz_sinirli(client, db_factory, reset_state, monkeypatch):
    monkeypatch.setenv("SHOWIS_MAX_BODY_BYTES", "1024")
    payload = b"x" * 4096
    for _ in range(8):
        res = await client.post(
            "/api/v1/cases", content=payload, headers={"Content-Type": "application/json"}
        )
        assert res.status_code == 413

    rows = await _audit_rows(db_factory, "request.rejected")
    assert len(rows) == 5
    assert all(row.details["status_code"] == 413 for row in rows)

"""T-354 (N-01): SSE slot kirası — terk edilen bağlantıda slot serbest kalır.

Kanıt (Burak T-351): Next proxy 3 SSE bağlantısını bırakınca (sekme kapatma)
sonraki bağlantılar **429**; doğrudan API'de reap çalışıyordu → sızıntı, akışın
`finally`'si hiç çalışmadığı için slotların asılı kalmasıydı.

Bu testler kira mekanizmasını kilitler:
(a) 3 terk edilmiş kira + 90 sn TTL → yeni bağlantı kabul,
(b) `is_disconnected()` True → slot bırakılır,
(c) yazım hatası (`OSError`) → slot bırakılır,
(d) normal kapanış → sayaç 0,
(e) 4. **canlı** bağlantı hâlâ 429 + `Retry-After`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from showis.api.v1 import scans as scans_api
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.main import app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCAN_ID = "scan-sse-lease"


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


@pytest.fixture(autouse=True)
def clean_leases(monkeypatch):
    """Kira saatini gerçek zamanla ilerleyen, ofset ile kaydırılabilir yapar.

    Sabit (`frozen`) saat akışın deadline hesabını kilitlerdi (sonsuz ping).
    """
    scans_api.reset_sse_leases()
    real = time.monotonic
    origin = real()
    clock = {"offset": 0.0, "base": origin}
    monkeypatch.setattr(
        scans_api, "_now", lambda: clock["base"] + clock["offset"] + (real() - origin)
    )
    yield clock
    scans_api.reset_sse_leases()


async def _seed_scan(factory, *, status: str = "running") -> None:
    async with factory() as session:
        session.add(
            DbCase(
                id="case-sse-lease",
                name="SSE Lease",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(DbTarget(case_id="case-sse-lease", first_name="Ada", last_name="Lovelace"))
        session.add(
            DbScan(id=SCAN_ID, case_id="case-sse-lease", mode="O2", status=status)
        )
        await session.commit()


class _FakeRequest:
    """`Request.is_disconnected` davranışını taklit eden minimal nesne."""

    def __init__(self, disconnected: bool = False):
        self._disconnected = disconnected
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self._disconnected


# ---------------------------------------------------------------------------
# (a) 3 terk edilmiş kira + TTL → yeni bağlantı kabul edilir
# ---------------------------------------------------------------------------


async def test_terk_edilen_kiralar_ttl_ile_dusurulur(client, db_factory, clean_leases, monkeypatch):
    await _seed_scan(db_factory)
    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.05")

    # 3 kira alınır ve "terk edilir" (release çağrılmaz — proxy senaryosu)
    for _ in range(scans_api._MAX_SSE_CONNECTIONS):
        scans_api.acquire_sse_lease(SCAN_ID)
    assert scans_api.active_sse_leases(SCAN_ID) == 3

    # TTL dolmadan 429
    res = await client.get(f"/api/v1/scans/{SCAN_ID}/events")
    assert res.status_code == 429

    # 91 sn sonra: yeni istek terk edilmiş kiraları düşürür → kabul
    clean_leases["offset"] += 91
    async with client.stream("GET", f"/api/v1/scans/{SCAN_ID}/events") as response:
        assert response.status_code == 200, response.text
        _body = "".join([chunk async for chunk in response.aiter_text()])

    assert scans_api.active_sse_leases(SCAN_ID) == 0  # akış bitti → temiz


async def test_kira_yenilenirse_ttl_dusurmez(clean_leases):
    lease = scans_api.acquire_sse_lease(SCAN_ID)
    clean_leases["offset"] += 80
    scans_api.renew_sse_lease(lease)  # ping/olay yenilemesi
    clean_leases["offset"] += 80  # ilk kira 160 sn önce ama son yenileme 80 sn önce

    assert scans_api.reap_stale_sse_leases(SCAN_ID) == 0
    assert scans_api.active_sse_leases(SCAN_ID) == 1


# ---------------------------------------------------------------------------
# (b) is_disconnected True → slot bırakılır
# ---------------------------------------------------------------------------


async def test_disconnect_algilaninca_slot_birakilir(clean_leases):
    request = _FakeRequest(disconnected=True)
    lease = scans_api.acquire_sse_lease(SCAN_ID)

    async def _consume() -> list[str]:
        chunks: list[str] = []
        async for chunk in scans_api._scan_event_stream(
            SCAN_ID, "running", request=request, lease=lease
        ):
            chunks.append(chunk)
        return chunks

    chunks = await asyncio.wait_for(_consume(), timeout=5.0)

    assert request.calls >= 1
    # Yalnız açılış yorumu gitti; kopukluk ilk turda yakalandı
    assert chunks == [": connected\n\n"]
    assert scans_api.active_sse_leases(SCAN_ID) == 0
    assert scans_api._sse_connections.get(SCAN_ID) is None


# ---------------------------------------------------------------------------
# (c) Yazım hatası (OSError) → slot bırakılır
# ---------------------------------------------------------------------------


async def test_yazim_hatasinda_slot_birakilir(clean_leases, monkeypatch):
    lease = scans_api.acquire_sse_lease(SCAN_ID)

    def _boom(*_args: Any, **_kwargs: Any) -> str:
        raise OSError("baglanti koptu")

    monkeypatch.setattr(scans_api, "_sse_payload", _boom)

    async def _consume() -> list[str]:
        chunks: list[str] = []
        async for chunk in scans_api._scan_event_stream(
            SCAN_ID, "completed", lease=lease
        ):
            chunks.append(chunk)
        return chunks

    assert await asyncio.wait_for(_consume(), timeout=5.0) == []
    assert scans_api.active_sse_leases(SCAN_ID) == 0


# ---------------------------------------------------------------------------
# (d) Normal kapanış → sayaç 0
# ---------------------------------------------------------------------------


async def test_normal_kapanista_slot_sifirlanir(client, db_factory, clean_leases, monkeypatch):
    await _seed_scan(db_factory, status="completed")
    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.05")

    async with client.stream("GET", f"/api/v1/scans/{SCAN_ID}/events") as response:
        assert response.status_code == 200
        assert response.headers["x-scan-status"] == "completed"  # T-354 tanı başlığı
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert "event: scan.completed" in body
    assert scans_api.active_sse_leases(SCAN_ID) == 0
    assert scans_api._sse_connections.get(SCAN_ID) is None


async def test_sure_asiminda_slot_birakilir(client, db_factory, clean_leases, monkeypatch):
    await _seed_scan(db_factory)
    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.05")

    async with client.stream("GET", f"/api/v1/scans/{SCAN_ID}/events") as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert ": bye" in body
    assert scans_api.active_sse_leases(SCAN_ID) == 0


# ---------------------------------------------------------------------------
# (e) 4. canlı bağlantı 429 + Retry-After
# ---------------------------------------------------------------------------


async def test_dorduncu_canli_baglanti_429_ve_retry_after(client, db_factory, clean_leases):
    await _seed_scan(db_factory)
    for _ in range(scans_api._MAX_SSE_CONNECTIONS):
        scans_api.acquire_sse_lease(SCAN_ID)

    res = await client.get(f"/api/v1/scans/{SCAN_ID}/events")
    assert res.status_code == 429
    assert res.headers["retry-after"] == str(scans_api._SSE_RETRY_AFTER_SECONDS)
    detail = res.json()["detail"]
    assert f"{scans_api._SSE_RETRY_AFTER_SECONDS} saniye sonra" in detail
    # Reddedilen istek slot tüketmemeli
    assert scans_api.active_sse_leases(SCAN_ID) == scans_api._MAX_SSE_CONNECTIONS


async def test_kira_birakilinca_yeni_baglanti_kabul(client, db_factory, clean_leases, monkeypatch):
    await _seed_scan(db_factory)
    monkeypatch.setenv("SHOWIS_SSE_MAX_SECONDS", "0.05")

    leases = [scans_api.acquire_sse_lease(SCAN_ID) for _ in range(3)]
    scans_api.release_sse_lease(leases[0])

    async with client.stream("GET", f"/api/v1/scans/{SCAN_ID}/events") as response:
        assert response.status_code == 200
        _ = "".join([chunk async for chunk in response.aiter_text()])

    assert scans_api.active_sse_leases(SCAN_ID) == 2  # iki kira hâlâ asılı


async def test_olmayan_scan_404_ve_slot_tuketmez(client, db_factory, clean_leases):
    res = await client.get("/api/v1/scans/yok-boyle-scan/events")
    assert res.status_code == 404
    assert scans_api.active_sse_leases("yok-boyle-scan") == 0

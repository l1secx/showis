"""Scanner paralel yurutme testleri (T-101).

Kapsam:
* (a) es zamanlilik kazanci: concurrency 1 vs 4,
* (b) ``SHOWIS_SCAN_CONCURRENCY=1`` eski sirali davranisi korur,
* (c) butce asiminda kalan gorevler ``skipped`` isaretlenir,
* (d) contextvar istek sayaci paralel gorevlerde izole,
* (e) bir gorevin hatasi digerlerini durdurmaz,
* (f) iptal: yeni gorev dispatch edilmez, calisanlar tamamlanir.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest
import respx
from showis.db.base import Base
from showis.db.models import AuditLog
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.models import Task as DbTask
from showis.services.orchestrator import event_broadcaster
from showis.services.scanner import ScannerService
from showis_contracts import (
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
)
from showis_engine.http import (
    HttpClient,
    count_requests_scope,
    request_marker,
    requests_made,
    requests_since,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Yardimcilar
# ---------------------------------------------------------------------------


async def _make_scan(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    budget: dict | None = None,
    scan_id: str = "scan-par",
    case_id: str = "case-par",
) -> tuple[str, str]:
    async with session_factory() as session:
        session.add_all(
            [
                DbCase(
                    id=case_id,
                    name="Paralel Tarama",
                    purpose="test",
                    legal_basis="self_check",
                    retention_days=7,
                ),
                DbTarget(
                    case_id=case_id,
                    first_name="Ada",
                    last_name="Lovelace",
                    hints={},
                    review_required=False,
                ),
                DbScan(
                    id=scan_id,
                    case_id=case_id,
                    mode="O2",
                    status="queued",
                    budget=budget or {},
                ),
            ]
        )
        await session.commit()
    return case_id, scan_id


#: `execute_scan` icin ust sinir: uretimde deadlock olursa test ASILMAZ, FAIL eder.
SCAN_TIMEOUT_S = 20.0


async def _execute_scan(scanner: ScannerService, scan_id: str, session: AsyncSession):
    """`execute_scan`'i sinirli sure bekler (T-312: CI'da asili kalmayi onler)."""
    return await asyncio.wait_for(
        scanner.execute_scan(scan_id, session), timeout=SCAN_TIMEOUT_S
    )


async def _load_scan(session_factory: async_sessionmaker[AsyncSession], scan_id: str) -> DbScan:
    async with session_factory() as session:
        row = (await session.execute(select(DbScan).where(DbScan.id == scan_id))).scalar_one()
        return row


async def _task_rows(
    session_factory: async_sessionmaker[AsyncSession], scan_id: str
) -> list[DbTask]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(DbTask).where(DbTask.scan_id == scan_id).order_by(DbTask.id)
                )
            )
            .scalars()
            .all()
        )


class SlowConnector:
    """Sabit gecikmeli sahte connector; es zamanli calisan gorev sayisini izler."""

    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A

    def __init__(
        self,
        name: str = "slowconn",
        tasks: int = 8,
        delay: float = 0.2,
        fail_on: set[str] | None = None,
        target_active: int = 1,
    ):
        self.name = name
        self.task_count = tasks
        self.delay = delay
        self.fail_on = fail_on or set()
        self.active = 0
        self.max_active = 0
        #: `active` bu sayiya ulasinca set edilir: testler `sleep` yerine bunu
        #: bekler (T-312 — deterministik "gorevler ucusta" kosulu).
        self.target_active = target_active
        self.started = asyncio.Event()
        #: `CancelledError` ile kesilen gorev sayisi (iptal gercekten isledi mi).
        self.interrupted = 0

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        return [
            TaskSpec(
                id=f"{self.name}-{i}",
                connector=self.name,
                platform=self.platform,
                kind="probe",
                params={"i": i},
            )
            for i in range(self.task_count)
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= self.target_active:
            self.started.set()
        try:
            await asyncio.sleep(self.delay)
            if task.id in self.fail_on:
                raise RuntimeError("connector boom")
            subject = f"user{task.params['i']}"
            return [
                Observation(
                    id=f"obs-{task.id}",
                    connector=self.name,
                    platform=self.platform,
                    kind=ObservationKind.PROFILE,
                    subject=subject,
                    tier=self.tier,
                    data={"login": subject},
                )
            ]
        except asyncio.CancelledError:
            # Iptal kanali gercekten calisti: gorev uykusunda kesildi.
            self.interrupted += 1
            raise
        finally:
            self.active -= 1

    def normalize(self, obs: Observation) -> tuple[list[Entity], list]:
        return (
            [
                Entity(
                    id=f"{self.platform.value}:{obs.subject}",
                    type=EntityType.ACCOUNT,
                    platform=self.platform,
                    label=obs.subject,
                    attributes={"username": obs.subject},
                )
            ],
            [],
        )


class HttpConnector:
    """Gorev basina 2 gercek (mock) HTTP istegi atan sahte connector."""

    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A

    def __init__(self, name: str = "httpconn", tasks: int = 6):
        self.name = name
        self.task_count = tasks
        self.client = HttpClient(enable_ssrf_guard=False)

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        return [
            TaskSpec(
                id=f"{self.name}-{i}",
                connector=self.name,
                platform=self.platform,
                kind="probe",
                params={"i": i},
            )
            for i in range(self.task_count)
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        index = task.params["i"]
        await self.client.get(f"https://site{index}.example.com/p1")
        await self.client.get(f"https://site{index}.example.com/p2")
        return []

    def normalize(self, obs: Observation) -> tuple[list[Entity], list]:
        return [], []


def _register_http_routes(router: respx.Router, hosts: int) -> None:
    for index in range(hosts):
        router.get(f"https://site{index}.example.com/robots.txt").respond(404)
        router.get(f"https://site{index}.example.com/p1").respond(200, text="ok")
        router.get(f"https://site{index}.example.com/p2").respond(200, text="ok")


@pytest.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# (a) Es zamanlilik kazanci + SSE kaybi yok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iki_yavas_connector_paralel_calisir(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "4")
    _, scan_id = await _make_scan(session_factory)
    connector = SlowConnector(tasks=8, delay=0.2)
    sequential_cost = connector.task_count * connector.delay

    queue = event_broadcaster.subscribe(scan_id)
    try:
        async with session_factory() as session:
            scanner = ScannerService(connectors=[connector])
            started = time.perf_counter()
            await _execute_scan(scanner, scan_id, session)
            elapsed = time.perf_counter() - started

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        event_broadcaster.unsubscribe(scan_id, queue)

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == connector.task_count
    # Paralel yurutme sirali maliyetin %70'inin altinda kalmali
    assert elapsed < sequential_cost * 0.7, f"{elapsed:.2f}s >= {sequential_cost * 0.7:.2f}s"
    # Gercekten es zamanli calisti mi (en az 2 gorev ayni anda uctaydi)
    assert connector.max_active >= 2

    # SSE kaybi yok: her gorev icin started + finished
    started_events = [e for e in events if e["type"] == "task.started"]
    finished_events = [e for e in events if e["type"] == "task.finished"]
    assert len(started_events) == connector.task_count
    assert len(finished_events) == connector.task_count


# ---------------------------------------------------------------------------
# (b) concurrency=1 -> eski sirali davranis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_bir_sirali_davranis(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "1")
    _, scan_id = await _make_scan(session_factory)
    connector = SlowConnector(tasks=5, delay=0.1)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        started = time.perf_counter()
        await _execute_scan(scanner, scan_id, session)
        elapsed = time.perf_counter() - started

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == 5
    # Ayni anda tek gorev uctu
    assert connector.max_active == 1
    assert elapsed >= 5 * 0.1 * 0.9

    # started_at / finished_at gercekten yaziliyor (T-102 kalibrasyonu)
    for row in await _task_rows(session_factory, scan_id):
        assert row.started_at is not None
        assert row.finished_at is not None
        assert row.started_at <= row.finished_at


# ---------------------------------------------------------------------------
# (c) Butce asimi -> kalan gorevler skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butce_asiminda_kalan_gorevler_skipped(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "4")
    _, scan_id = await _make_scan(
        session_factory,
        budget={"max_tasks": 3, "max_http_requests": 10_000, "max_wall_seconds": 300},
    )
    connector = SlowConnector(tasks=8, delay=0.02)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await _execute_scan(scanner, scan_id, session)

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == 3
    assert scan.stats["skipped_tasks"] == 5

    rows = await _task_rows(session_factory, scan_id)
    statuses = {row.status for row in rows}
    assert statuses == {"done", "skipped"}
    assert sum(1 for row in rows if row.status == "skipped") == 5


# ---------------------------------------------------------------------------
# (d) contextvar istek sayaci izolasyonu
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvar_istek_sayaci_gorevler_arasinda_izole():
    client = HttpClient(enable_ssrf_guard=False, use_host_limiter=False)
    before_total = requests_made()

    async def worker(index: int) -> tuple[int, int]:
        with count_requests_scope() as scope:
            marker = request_marker()
            await client.get(f"https://iso{index}.example.com/a")
            after_first = requests_since(marker)
            await client.get(f"https://iso{index}.example.com/b")
            return scope.count, after_first

    with respx.mock(assert_all_called=False) as router:
        for index in range(3):
            router.get(f"https://iso{index}.example.com/robots.txt").respond(404)
            router.get(f"https://iso{index}.example.com/a").respond(200, text="ok")
            router.get(f"https://iso{index}.example.com/b").respond(200, text="ok")
        results = await asyncio.gather(*(worker(index) for index in range(3)))

    # Her gorev yalnizca kendi 2 istegini sayar (global sayac paralelde bozulurdu)
    assert [count for count, _ in results] == [2, 2, 2]
    assert [first for _, first in results] == [1, 1, 1]
    # Geriye donuk uyumlu surec geneli sayac toplami korur
    assert requests_made() - before_total == 6


@pytest.mark.asyncio
async def test_paralel_http_istekleri_gorev_basina_sayilir(session_factory, monkeypatch):
    """Eski surec-global sayac paralelde sisirirdi; butce artik dogru olculur."""
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "4")
    _, scan_id = await _make_scan(session_factory)
    connector = HttpConnector(tasks=6)

    with respx.mock(assert_all_called=False) as router:
        _register_http_routes(router, hosts=connector.task_count)
        async with session_factory() as session:
            scanner = ScannerService(connectors=[connector])
            await _execute_scan(scanner, scan_id, session)

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == 6
    # 6 gorev x 2 istek = 12 (robots.txt sayilmaz)
    assert scan.stats["http_requests"] == 12


# ---------------------------------------------------------------------------
# (e) Gorev hatasi digerlerini durdurmaz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gorev_hatasi_digerlerini_durdurmaz(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "3")
    _, scan_id = await _make_scan(session_factory)
    connector = SlowConnector(tasks=6, delay=0.02, fail_on={"slowconn-2"})

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await _execute_scan(scanner, scan_id, session)

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == 6

    rows = {row.id: row for row in await _task_rows(session_factory, scan_id)}
    assert rows["slowconn-2"].status == "failed"
    assert "connector boom" in (rows["slowconn-2"].error or "")
    # Hatada da bitis ani yazilir (sure olcumu icin)
    assert rows["slowconn-2"].finished_at is not None
    assert sum(1 for row in rows.values() if row.status == "done") == 5


# ---------------------------------------------------------------------------
# (f) Iptal: yeni gorev dispatch edilmez, calisanlar tamamlanir
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iptalde_yeni_gorev_dispatch_edilmez(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "2")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cancel.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        _, scan_id = await _make_scan(factory)
        connector = SlowConnector(tasks=8, delay=0.15, target_active=2)

        async with factory() as session:
            scanner = ScannerService(connectors=[connector])
            scan_task = asyncio.create_task(scanner.execute_scan(scan_id, session))
            # Ilk 2 gorev gercekten uctayken iptal gelir (sabit sleep yok — T-312)
            await asyncio.wait_for(connector.started.wait(), timeout=SCAN_TIMEOUT_S)
            async with factory() as cancel_session:
                row = (
                    await cancel_session.execute(select(DbScan).where(DbScan.id == scan_id))
                ).scalar_one()
                row.status = "cancelled"
                await cancel_session.commit()
            await asyncio.wait_for(scan_task, timeout=SCAN_TIMEOUT_S)

        scan = await _load_scan(factory, scan_id)
        assert scan.status == "cancelled"
        # Yeni gorev dispatch edilmedi: executed + cancelled + skipped = toplam
        executed = scan.stats["executed_tasks"]
        cancelled = scan.stats["cancelled_tasks"]
        assert executed + cancelled + scan.stats["skipped_tasks"] == connector.task_count
        assert executed < connector.task_count
        rows = await _task_rows(factory, scan_id)
        assert all(row.status in {"done", "skipped"} for row in rows)
        assert sum(1 for row in rows if row.status == "skipped") == (
            connector.task_count - executed
        )
    finally:
        ScannerService.clear_cancel(scan_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_iptal_kanali_ucustaki_gorevleri_keser(session_factory, monkeypatch):
    """`ScannerService.cancel()` uzun gorevler bitmeden donguyu uyandirir.

    T-312 (asilma teshisi): eski surum `await asyncio.sleep(0.05)` ile "gorevler
    uctadir" varsayiyordu; iptal sinyali dongunun fazina gore bazen
    `asyncio.wait`'e varmadan geliyor ve test 0.5 sn'lik gorev suresini bekliyordu
    (olculen: ~0.516 sn, 10 kosuda 6 kez). Artik:

    * bekleme **kosula** bagli: `connector.started` event'i iki gorev de
      `run()` icine girince set edilir; bu fazda scanner dongusu `asyncio.wait`
      icinde park etmis olur (bkz. `_dispatch` sonrasi), yani iptal sinyali
      her zaman "bekleyen" kanala duser,
    * her await `asyncio.wait_for` ile sinirli: uretimde deadlock olsa bile test
      asilmaz, 10 sn'de FAIL eder (CI'yi kilitlemez),
    * iptalin gercekten **kesme** oldugu `connector.interrupted` ile kanitlanir
      (gorevler uykuda iptal edildiyse 2; normal biterse 0).
    """
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "2")
    case_id, scan_id = await _make_scan(session_factory)
    connector = SlowConnector(tasks=8, delay=0.5, target_active=2)

    try:
        async with session_factory() as session:
            scanner = ScannerService(connectors=[connector])
            scan_task = asyncio.create_task(scanner.execute_scan(scan_id, session))
            # 2 gorev ucusta + scanner dongusu beklemede
            await asyncio.wait_for(connector.started.wait(), timeout=10.0)
            started = time.perf_counter()
            ScannerService.cancel(scan_id)
            await asyncio.wait_for(scan_task, timeout=10.0)
            elapsed = time.perf_counter() - started

        # Iptal, 0.5 sn'lik uykulari beklemeden etkili oldu:
        # dogal bitis 8 gorev / 2 es zamanli * 0.5 sn = 2.0 sn olurdu.
        assert elapsed < 1.0, f"iptal {elapsed:.2f}s surdu (gorevler kesilmedi)"
        assert connector.interrupted == 2, (
            f"ucustaki 2 gorev uykuda kesilmeliydi (interrupted={connector.interrupted})"
        )

        scan = await _load_scan(session_factory, scan_id)
        assert scan.status == "cancelled"
        assert scan.stats["cancelled_tasks"] == 2  # ucustaki 2 gorev kesildi
        assert scan.stats["executed_tasks"] == 0
        assert scan.stats["skipped_tasks"] == 6  # 6 gorev hic baslamadi

        rows = await _task_rows(session_factory, scan_id)
        assert sum(1 for row in rows if row.error == "cancelled") == 2

        # Audit: iptal edilen tarama `scan.completed` YAZMAZ
        async with session_factory() as session:
            actions = list(
                (
                    await session.execute(
                        select(AuditLog.action).where(AuditLog.case_id == case_id)
                    )
                )
                .scalars()
                .all()
            )
        assert "scan.completed" not in actions
        assert "scan.cancelled" in actions
    finally:
        ScannerService.clear_cancel(scan_id)


# ---------------------------------------------------------------------------
# (g) SEC-09 butce guard: limit asilinca istek ATILMAZ (fail-closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butce_guard_istek_atmadan_keser(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "2")
    _, scan_id = await _make_scan(
        session_factory,
        budget={"max_tasks": 100, "max_http_requests": 3, "max_wall_seconds": 300},
    )
    connector = HttpConnector(tasks=6)  # her gorev 2 istek ister -> toplam 12

    with respx.mock(assert_all_called=False) as router:
        routes = []
        for index in range(connector.task_count):
            router.get(f"https://site{index}.example.com/robots.txt").respond(404)
            routes.append(
                router.get(f"https://site{index}.example.com/p1").respond(200, text="ok")
            )
            routes.append(
                router.get(f"https://site{index}.example.com/p2").respond(200, text="ok")
            )
        async with session_factory() as session:
            scanner = ScannerService(connectors=[connector])
            await _execute_scan(scanner, scan_id, session)

        sent = sum(route.call_count for route in routes)

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"  # tarama devam eder, sadece istekler kesilir
    assert sent == 3, f"butce asiminda {sent} istek atildi (beklenen 3)"
    assert scan.stats["http_requests"] == 3
    # Kesilen gorevler failed, kalanlar skipped
    rows = await _task_rows(session_factory, scan_id)
    assert sum(1 for row in rows if row.status == "failed") >= 1
    assert sum(1 for row in rows if row.status == "skipped") >= 1


# ---------------------------------------------------------------------------
# (h) SEC-15: kullanici karari yeniden yazimda korunur
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kullanici_karari_yeniden_yazimda_korunur(session_factory):
    from showis.db.models import Entity as DbEntity
    from showis.db.models import Finding as DbFinding
    from showis_contracts import Target as ContractTarget

    case_id, scan_id = await _make_scan(session_factory)
    entity_id = "github:kararli"
    finding_id = f"finding:{entity_id}"
    verified = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    async with session_factory() as session:
        case = await session.get(DbCase, case_id)
        scan = await session.get(DbScan, scan_id)
        assert case is not None and scan is not None
        session.add(
            DbEntity(
                id=entity_id,
                case_id=case_id,
                type="account",
                platform="github",
                label="kararli",
                attributes={"username": "kararli"},
            )
        )
        session.add(
            DbFinding(
                id=finding_id,
                case_id=case_id,
                scan_id=scan_id,
                entity_id=entity_id,
                platform="github",
                label="kararli",
                status="rejected",
                score=0.01,
                breakdown={"name": 0.01},
                explanation="kullanici reddetti",
                evidence_ids=[],
                verified_at=verified,
            )
        )
        await session.commit()

        entity = Entity(
            id=entity_id,
            type=EntityType.ACCOUNT,
            platform=Platform.GITHUB,
            label="kararli",
            attributes={"username": "kararli", "name": "Kararli Kisi"},
        )
        scanner = ScannerService(connectors=[])
        await scanner._write_findings(
            session,
            scan,
            case,
            ContractTarget(first_name="Kararli", last_name="Kisi"),
            [entity],
            set(),
            notify=False,
            explain=False,
        )

    async with session_factory() as session:
        row = await session.get(DbFinding, finding_id)
        assert row is not None
        # Kullanici karari korunur (sqlite tz bilgisini saklamaz -> naive karsilastir)
        assert row.status == "rejected"
        assert row.verified_at is not None
        assert row.verified_at.replace(tzinfo=None) == verified.replace(tzinfo=None)
        # Skor/aciklama yine guncellenir
        assert row.score != 0.01

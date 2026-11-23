"""T-331: paralel tarama motoru için dayanıklılık (soak) testleri.

Sahte connector'lar (ağ yok), hızlandırılmış senaryolar. Her senaryo
`asyncio.wait_for(..., 60)` ile korunur — test asla asılı kalmaz (T-312 dersi).

Değişmezler (invariants):
* her görev terminal durumda (`done|failed|skipped`), askıda `pending/running` yok,
* `stats["executed_tasks"]` == done + failed,
* bulgu tablosunda `finding.id` **tekrar etmez** (çift yazım yok),
* entity sayısı üretilen benzersiz entity sayısına eşit,
* sağlıklı koşularda tarama `completed`, iptalde `cancelled` (≤ ~1 sn içinde),
* `scan.completed` audit/yayını iptal edilen taramada **yok**.

Dosya toplam süresi < 30 sn hedeflenir (gecikmeler 0-50 ms).
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid

import pytest
from showis.db.base import Base
from showis.db.models import AuditLog
from showis.db.models import Case as DbCase
from showis.db.models import Entity as DbEntity
from showis.db.models import Finding as DbFinding
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
from showis_engine.http import HttpClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

#: Her senaryo için üst sınır — motor kilitlenirse test başarısız olur (asılı kalmaz)
SCENARIO_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Altyapı
# ---------------------------------------------------------------------------


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


async def _make_scan(
    factory: async_sessionmaker[AsyncSession],
    *,
    case_id: str,
    scan_id: str,
    budget: dict | None = None,
) -> None:
    async with factory() as session:
        session.add(
            DbCase(
                id=case_id,
                name=f"Soak {case_id}",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(DbTarget(case_id=case_id, first_name="Ada", last_name="Lovelace", hints={}))
        session.add(
            DbScan(id=scan_id, case_id=case_id, mode="O2", status="queued", budget=budget or {})
        )
        await session.commit()


class SoakConnector:
    """N görev üreten, rastgele gecikmeli/hata oranlı sahte connector.

    Her görev benzersiz bir kullanıcı adı üretir; normalize tek bir entity
    döndürür (`platform:<username>`), böylece entity/bulgu tekilliği ölçülebilir.
    """

    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A

    def __init__(
        self,
        *,
        name: str = "soak",
        tasks: int,
        max_delay: float = 0.05,
        failure_rate: float = 0.0,
        seed: int = 7,
        http_requests_per_task: int = 0,
    ):
        self.name = name
        self.task_count = tasks
        # Gerçek planda görev id'leri uuid'dir; yeniden taramada PK çakışması
        # olmasın diye her örnek kendi çalışma kimliğini taşır.
        self._run_id = uuid.uuid4().hex[:8]
        self.max_delay = max_delay
        self._rng = random.Random(seed)
        #: Deterministik hata seti: `failure_rate` yalnız bu seti üretmek için
        #: kullanılır (görev içinde rastgelelik YOK → koşular tekrarlanabilir).
        self.fail_indexes: set[int] = (
            {
                index
                for index in range(tasks)
                if random.Random(seed * 1000 + index).random() < failure_rate
            }
            if failure_rate
            else set()
        )
        self.http_requests_per_task = http_requests_per_task
        # Gerçek istek atmayan istemci: yalnız butce sayaci icin
        self._client = HttpClient(enable_ssrf_guard=False, use_host_limiter=False)
        self.run_calls = 0
        #: Eşzamanlı çalışan görev sayısı + testlerin beklediği "uçuşta" koşulu
        self.active = 0
        self.target_active = 0
        self.started = asyncio.Event()
        #: `CancelledError` ile kesilen görev sayısı (iptal gerçekten işledi mi)
        self.interrupted = 0

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        return [
            TaskSpec(
                id=f"{self.name}-{self._run_id}-{index}",
                connector=self.name,
                platform=self.platform,
                kind="probe",
                params={"i": index},
            )
            for index in range(self.task_count)
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        self.run_calls += 1
        index = int(task.params["i"])
        self.active += 1
        if self.target_active and self.active >= self.target_active:
            # Testler `sleep` yerine bu koşulu bekler (T-312 deseni):
            # bu fazda scanner döngüsü `asyncio.wait` içinde park etmiştir.
            self.started.set()
        try:
            await asyncio.sleep(self._rng.uniform(0, self.max_delay))
            if index in self.fail_indexes:
                raise RuntimeError(f"soak boom {index}")
            subject = f"user{index}"
            return [
                Observation(
                    id=f"obs-{task.id}",
                    connector=self.name,
                    platform=self.platform,
                    kind=ObservationKind.PROFILE,
                    subject=subject,
                    tier=self.tier,
                    data={"login": subject, "username": subject},
                )
            ]
        except asyncio.CancelledError:
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


class SlowConnector(SoakConnector):
    """İptal senaryosu: uzun uyuyan görevler (fırtına sırasında uçuşta kalsın).

    `target_active` kadar görev `run()` içine girince `started` event'i set edilir;
    test iptali buna göre verir (zamanlamaya bağlı `sleep` YOK).
    """

    def __init__(
        self,
        *,
        name: str = "slowsoak",
        tasks: int,
        delay: float = 0.4,
        target_active: int = 1,
    ):
        super().__init__(name=name, tasks=tasks, max_delay=0.0)
        self.delay = delay
        self.target_active = target_active

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        self.run_calls += 1
        self.active += 1
        if self.active >= self.target_active:
            self.started.set()
        try:
            await asyncio.sleep(self.delay)
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
            self.interrupted += 1
            raise
        finally:
            self.active -= 1


async def _load_scan(factory: async_sessionmaker[AsyncSession], scan_id: str) -> DbScan:
    async with factory() as session:
        return (
            await session.execute(select(DbScan).where(DbScan.id == scan_id))
        ).scalar_one()


async def _tasks(factory: async_sessionmaker[AsyncSession], scan_id: str) -> list[DbTask]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(DbTask).where(DbTask.scan_id == scan_id).order_by(DbTask.id)
                )
            )
            .scalars()
            .all()
        )


async def _finding_ids(factory: async_sessionmaker[AsyncSession], case_id: str) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(DbFinding.id).where(DbFinding.case_id == case_id)
                )
            )
            .scalars()
            .all()
        )


async def _entity_ids(factory: async_sessionmaker[AsyncSession], case_id: str) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(select(DbEntity.id).where(DbEntity.case_id == case_id))
            )
            .scalars()
            .all()
        )


async def _audit_actions(factory: async_sessionmaker[AsyncSession], case_id: str) -> list[str]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.case_id == case_id)
                )
            )
            .scalars()
            .all()
        )


def _assert_terminal(tasks: list[DbTask]) -> None:
    statuses = {task.status for task in tasks}
    assert statuses <= {"done", "failed", "skipped"}, f"terminal olmayan durum: {statuses}"
    unfinished = [task.id for task in tasks if task.status in {"pending", "running"}]
    assert not unfinished, f"askıda görev kaldı: {unfinished}"


# ---------------------------------------------------------------------------
# 1. Yük: 100 görev, concurrency 8, rastgele gecikme
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yuz_gorev_concurrency_sekiz(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "8")
    case_id, scan_id = "soak-load-case", "soak-load-scan"
    await _make_scan(session_factory, case_id=case_id, scan_id=scan_id)
    connector = SoakConnector(tasks=100, max_delay=0.05)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await asyncio.wait_for(scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT)

    scan = await _load_scan(session_factory, scan_id)
    tasks = await _tasks(session_factory, scan_id)
    assert len(tasks) == 100
    _assert_terminal(tasks)

    done = sum(1 for task in tasks if task.status == "done")
    failed = sum(1 for task in tasks if task.status == "failed")
    assert done == 100 and failed == 0
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == done + failed
    assert scan.stats["skipped_tasks"] == 0

    # Çift bulgu yok + entity tekilliği
    finding_ids = await _finding_ids(session_factory, case_id)
    assert len(finding_ids) == len(set(finding_ids)) == 100
    entity_ids = await _entity_ids(session_factory, case_id)
    assert len(entity_ids) == len(set(entity_ids)) == 100


# ---------------------------------------------------------------------------
# 2. Rastgele hata: görevlerin %20'si patlar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rastgele_gorev_hatalari_taramayi_durdurmaz(session_factory, monkeypatch):
    """Görevlerin ~%20'si hata verir (deterministik set → tekrarlanabilir koşu)."""
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "6")
    case_id, scan_id = "soak-err-case", "soak-err-scan"
    await _make_scan(session_factory, case_id=case_id, scan_id=scan_id)
    connector = SoakConnector(tasks=60, max_delay=0.01, failure_rate=0.2, seed=13)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await asyncio.wait_for(scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT)

    scan = await _load_scan(session_factory, scan_id)
    tasks = await _tasks(session_factory, scan_id)
    _assert_terminal(tasks)

    done = sum(1 for task in tasks if task.status == "done")
    failed = sum(1 for task in tasks if task.status == "failed")
    assert failed > 0, "senaryo gereği en az bir görev patlamalı"
    assert done + failed == 60
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == done + failed
    # Hatalı görevlerde de bitiş anı ve mesaj var
    for task in tasks:
        if task.status == "failed":
            assert "soak boom" in (task.error or "")
            assert task.finished_at is not None

    finding_ids = await _finding_ids(session_factory, case_id)
    assert len(finding_ids) == len(set(finding_ids)) == done


# ---------------------------------------------------------------------------
# 3. İptal fırtınası: rastgele anlarda iptal → cancelled kalır
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iptal_firtinasi_durumu_korur(session_factory, monkeypatch):
    """İptal fırtınası: durum `cancelled` kalır, uçuştakiler `skipped`+`cancelled`.

    Determinizm (T-345 / T-312 deseni): iptal, **zamanlamaya bağlı `sleep` ile
    değil**, connector'ın `started` Event'i ile verilir — event, `concurrency`
    kadar görev `run()` içine girdiğinde set edilir; bu fazda scanner döngüsü
    `asyncio.wait` içinde park etmiştir, yani iptal sinyali her zaman bekleyen
    kanala düşer. Tüm beklemeler `asyncio.wait_for` ile sınırlıdır.
    """
    concurrency = 3
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", str(concurrency))
    case_id, scan_id = "soak-cancel-case", "soak-cancel-scan"
    await _make_scan(session_factory, case_id=case_id, scan_id=scan_id)
    connector = SlowConnector(tasks=40, delay=0.4, target_active=concurrency)

    events: list[dict] = []
    queue = event_broadcaster.subscribe(scan_id)

    started_at = time.perf_counter()
    try:
        async with session_factory() as session:
            scanner = ScannerService(connectors=[connector])
            scan_task = asyncio.create_task(scanner.execute_scan(scan_id, session))
            # Görevler gerçekten uçuşta (döngü asyncio.wait içinde)
            await asyncio.wait_for(connector.started.wait(), timeout=10.0)
            cancel_started = time.perf_counter()
            # Fırtına: aynı taramaya art arda iptal sinyalleri (idempotent olmalı)
            for _ in range(4):
                ScannerService.cancel(scan_id)
            await asyncio.wait_for(scan_task, timeout=10.0)
        elapsed = time.perf_counter() - started_at
        cancel_elapsed = time.perf_counter() - cancel_started
        while not queue.empty():
            events.append(queue.get_nowait())
    finally:
        event_broadcaster.unsubscribe(scan_id, queue)
        ScannerService.clear_cancel(scan_id)

    # 40 görev × 0.4 sn / 3 eşzamanlı ≈ 5.3 sn sürerdi; iptal çok daha erken bitmeli
    assert cancel_elapsed < 1.0, f"iptal {cancel_elapsed:.2f}s sürdü (kesme işlemedi)"
    assert elapsed < 3.0, f"tarama {elapsed:.2f}s sürdü"
    # Uçuştaki görevler gerçekten uykuda kesildi
    assert connector.interrupted == concurrency, (
        f"uçuştaki {concurrency} görev kesilmeliydi (interrupted={connector.interrupted})"
    )

    scan = await _load_scan(session_factory, scan_id)
    tasks = await _tasks(session_factory, scan_id)
    assert scan.status == "cancelled"
    _assert_terminal(tasks)

    cancelled = [task for task in tasks if (task.error or "") == "cancelled"]
    assert len(cancelled) == concurrency, f"cancelled={len(cancelled)}"
    assert scan.stats["cancelled_tasks"] == concurrency
    assert all(task.status == "skipped" for task in cancelled)
    assert scan.stats["executed_tasks"] == 0
    assert scan.stats["skipped_tasks"] == connector.task_count - concurrency

    # `scan.completed` yayınlanmadı; `scan.cancelled` yayınlandı
    types = [event["type"] for event in events]
    assert "scan.completed" not in types
    assert "scan.cancelled" in types

    actions = await _audit_actions(session_factory, case_id)
    assert "scan.completed" not in actions

    # Çift bulgu yok
    finding_ids = await _finding_ids(session_factory, case_id)
    assert len(finding_ids) == len(set(finding_ids))


# ---------------------------------------------------------------------------
# 4. Bütçe: max_tasks ve max_http_requests ayrı ayrı
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butce_max_tasks(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "4")
    case_id, scan_id = "soak-bt-case", "soak-bt-scan"
    await _make_scan(
        session_factory,
        case_id=case_id,
        scan_id=scan_id,
        budget={"max_tasks": 12, "max_http_requests": 10_000, "max_wall_seconds": 300},
    )
    connector = SoakConnector(tasks=40, max_delay=0.01)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await asyncio.wait_for(scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT)

    scan = await _load_scan(session_factory, scan_id)
    tasks = await _tasks(session_factory, scan_id)
    _assert_terminal(tasks)

    done = sum(1 for task in tasks if task.status == "done")
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == 12
    assert done == 12
    # max_tasks aşımı yok: uçan görevler sayıldığı için tam 12
    assert scan.stats["executed_tasks"] <= 12
    assert scan.stats["skipped_tasks"] == 40 - 12


@pytest.mark.asyncio
async def test_butce_max_http_requests(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "3")
    case_id, scan_id = "soak-bh-case", "soak-bh-scan"
    await _make_scan(
        session_factory,
        case_id=case_id,
        scan_id=scan_id,
        budget={"max_tasks": 100, "max_http_requests": 2, "max_wall_seconds": 300},
    )
    connector = SoakConnector(tasks=20, max_delay=0.01)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await asyncio.wait_for(scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT)

    scan = await _load_scan(session_factory, scan_id)
    tasks = await _tasks(session_factory, scan_id)
    _assert_terminal(tasks)

    assert scan.status == "completed"
    # Bütçe HTTP katmanında fail-closed: en fazla `max_http_requests` kadar istek
    # sayılır (görev başına en az 1 sayım kuralı nedeniyle +=max(1, ...)), aşım ≤ concurrency
    assert scan.stats["http_requests"] <= 2 + 3
    assert scan.stats["executed_tasks"] < 20
    assert scan.stats["skipped_tasks"] > 0


# ---------------------------------------------------------------------------
# 5. Kenar durumlar: 0 ve 1 görev
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sifir_gorev(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "4")
    case_id, scan_id = "soak-zero-case", "soak-zero-scan"
    await _make_scan(session_factory, case_id=case_id, scan_id=scan_id)
    connector = SoakConnector(tasks=0)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await asyncio.wait_for(scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT)

    scan = await _load_scan(session_factory, scan_id)
    assert scan.status == "completed"
    assert scan.stats["executed_tasks"] == 0
    assert await _tasks(session_factory, scan_id) == []


@pytest.mark.asyncio
async def test_tek_gorev(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "8")
    case_id, scan_id = "soak-one-case", "soak-one-scan"
    await _make_scan(session_factory, case_id=case_id, scan_id=scan_id)
    connector = SoakConnector(tasks=1)

    async with session_factory() as session:
        scanner = ScannerService(connectors=[connector])
        await asyncio.wait_for(scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT)

    scan = await _load_scan(session_factory, scan_id)
    tasks = await _tasks(session_factory, scan_id)
    assert scan.status == "completed"
    assert len(tasks) == 1 and tasks[0].status == "done"
    assert tasks[0].started_at is not None and tasks[0].finished_at is not None
    assert scan.stats["executed_tasks"] == 1


# ---------------------------------------------------------------------------
# 6. Art arda iki tarama aynı vakada: çift yazım / durum sızıntısı yok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ayni_vakada_ardisik_taramalar(session_factory, monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "5")
    case_id = "soak-seq-case"
    await _make_scan(session_factory, case_id=case_id, scan_id="soak-seq-1")

    # Aktif tarama tekilliği (0003): ikinci tarama ancak birincisi bitince açılır.
    for index, scan_id in enumerate(("soak-seq-1", "soak-seq-2")):
        if index == 1:
            async with session_factory() as session:
                session.add(
                    DbScan(
                        id=scan_id,
                        case_id=case_id,
                        mode="O2",
                        status="queued",
                        budget={},
                    )
                )
                await session.commit()
        # Her tarama connector'lari yeniden kurar (üretimdeki factory akışı)
        connector = SoakConnector(tasks=25, max_delay=0.01)
        async with session_factory() as session:
            scanner = ScannerService(connectors=[connector])
            await asyncio.wait_for(
                scanner.execute_scan(scan_id, session), timeout=SCENARIO_TIMEOUT
            )

    first = await _load_scan(session_factory, "soak-seq-1")
    second = await _load_scan(session_factory, "soak-seq-2")
    assert first.status == second.status == "completed"

    finding_ids = await _finding_ids(session_factory, case_id)
    assert len(finding_ids) == len(set(finding_ids)) == 25, "yeniden tarama bulgu çoğaltmamalı"
    entity_ids = await _entity_ids(session_factory, case_id)
    assert len(entity_ids) == len(set(entity_ids)) == 25

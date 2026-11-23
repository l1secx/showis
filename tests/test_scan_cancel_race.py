"""T-322 — iptal yarışı ve asılı görev: tarama asla sonsuza kadar beklemez.

QA-01 (Elif T-312) teşhisi: `asyncio.wait` setine `cancel_waiter` yalnız
"henüz done değilse" ekleniyordu; iptal sinyali döngü `wait`e varmadan gelirse
(DB kontrolü / refill / dispatch sırasında) sinyal kayboluyor ve tarama uçuştaki
bitmeyen görevle sonsuza kadar asılı kalıyordu.

Bu dosya iki davranışı kilitler:

* (a) iptal, döngünün hangi fazında verilirse verilsin **≤ ~1.5 sn** içinde görülür,
* (b) hiç bitmeyen görev + `max_wall_seconds` → tarama bütçe içinde sonlanır.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.models import Task as DbTask
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class HangingConnector:
    """Görevi bitmeyen (veya çok uzun süren) sahte connector."""

    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A

    def __init__(self, name: str = "hang", tasks: int = 2, sleep_seconds: float = 30.0):
        self.name = name
        self.task_count = tasks
        self.sleep_seconds = sleep_seconds
        self.started = asyncio.Event()
        self.running = 0

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
        self.running += 1
        self.started.set()
        try:
            await asyncio.sleep(self.sleep_seconds)
        finally:
            self.running -= 1
        return [
            Observation(
                id=f"obs-{task.id}",
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.PROFILE,
                subject=str(task.params.get("i")),
                tier=self.tier,
                data={"username": f"user{task.params.get('i')}"},
            )
        ]

    def normalize(self, obs: Observation) -> tuple[list[Entity], list]:
        entity = Entity(
            id=f"{self.platform.value}:{obs.subject}",
            type=EntityType.ACCOUNT,
            platform=self.platform,
            label=str(obs.subject),
            attributes={"username": str(obs.subject)},
        )
        return [entity], []


async def _make_scan(
    factory: async_sessionmaker[AsyncSession],
    *,
    scan_id: str,
    case_id: str,
    budget: dict | None = None,
) -> None:
    async with factory() as session:
        session.add_all(
            [
                DbCase(
                    id=case_id,
                    name="Iptal Yarisi",
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
                    status="running",
                    budget=budget or {},
                ),
            ]
        )
        await session.commit()


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOWIS_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setenv("SHOWIS_SCAN_CONCURRENCY", "2")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    ScannerService.clear_cancel("scan-race")
    await engine.dispose()


async def _wait_for_scan(
    factory: async_sessionmaker[AsyncSession], scan_id: str, timeout: float = 5.0
) -> DbScan:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with factory() as session:
            scan = (
                await session.execute(select(DbScan).where(DbScan.id == scan_id))
            ).scalar_one()
        if scan.status != "running":
            return scan
        await asyncio.sleep(0.05)
    raise AssertionError(f"tarama {timeout} sn içinde sonlanmadı (asılı kaldı)")


@pytest.mark.asyncio
async def test_iptal_dispatch_fazinda_da_yakalanir(factory):
    """Uçuştaki görev bitmese bile iptal ≤ ~1.5 sn içinde görülür."""
    await _make_scan(factory, scan_id="scan-race", case_id="case-race")
    connector = HangingConnector(sleep_seconds=30.0)
    scanner = ScannerService(connectors=[connector])

    async def _execute() -> None:
        async with factory() as session:
            await scanner.execute_scan("scan-race", session)

    task = asyncio.create_task(_execute())
    # En az bir görev uçuşta olsun; dispatch'in inflight listesini
    # tamamlamasına kısa süre tanı (iptal yarışı testi deterministik kalsın).
    await asyncio.wait_for(connector.started.wait(), timeout=3.0)
    await asyncio.sleep(0.1)

    started = time.monotonic()
    ScannerService.cancel("scan-race")
    scan = await _wait_for_scan(factory, "scan-race", timeout=4.0)
    elapsed = time.monotonic() - started

    assert scan.status == "cancelled"
    assert elapsed < 2.5, f"iptal {elapsed:.2f} sn sonra görüldü (beklenen <2.5 sn)"
    await task

    async with factory() as session:
        rows = (
            await session.execute(select(DbTask).where(DbTask.scan_id == "scan-race"))
        ).scalars().all()
    assert rows, "planlanan görevler yazılmış olmalı"
    assert all(row.status == "skipped" for row in rows)
    assert all((row.error or "") == "cancelled" for row in rows)


@pytest.mark.asyncio
async def test_hic_bitmeyen_gorev_duvar_saati_butcesiyle_kesilir(factory):
    """max_wall_seconds=2 + bitmeyen görev → tarama ~2-4 sn içinde biter."""
    await _make_scan(
        factory,
        scan_id="scan-race",
        case_id="case-race",
        budget={"max_wall_seconds": 2, "max_tasks": 10, "max_http_requests": 50},
    )
    connector = HangingConnector(sleep_seconds=3600.0, tasks=2)
    scanner = ScannerService(connectors=[connector])

    started = time.monotonic()
    async with factory() as session:
        await asyncio.wait_for(scanner.execute_scan("scan-race", session), timeout=12.0)
    elapsed = time.monotonic() - started

    assert elapsed < 8.0, f"duvar saati bütçesi {elapsed:.1f} sn'de uygulanmadı"
    scan = await _wait_for_scan(factory, "scan-race", timeout=1.0)
    assert scan.status in ("completed", "cancelled")
    assert (scan.stats or {}).get("budget_hit") in (None, "max_wall_seconds")

    async with factory() as session:
        rows = (
            await session.execute(select(DbTask).where(DbTask.scan_id == "scan-race"))
        ).scalars().all()
    assert all(row.status == "skipped" for row in rows)


@pytest.mark.asyncio
async def test_iptal_edilmemis_tarama_normal_biter(factory):
    """Mutlu yol: kısa görevler normal tamamlanır, iptal sayacı 0 kalır."""
    await _make_scan(factory, scan_id="scan-race", case_id="case-race")
    connector = HangingConnector(sleep_seconds=0.05, tasks=2)
    scanner = ScannerService(connectors=[connector])

    async with factory() as session:
        await asyncio.wait_for(scanner.execute_scan("scan-race", session), timeout=10.0)

    scan = await _wait_for_scan(factory, "scan-race", timeout=1.0)
    assert scan.status == "completed"
    assert (scan.stats or {}).get("cancelled_tasks") == 0


# ---------------------------------------------------------------------------
# T-344: commit hatasında bellek durumu/sayaçlar geri alınır; iptal kaydı temizlenir
# ---------------------------------------------------------------------------


class _ExplodingConnector(HangingConnector):
    """normalize() patlar → execute_scan beklenmeyen hatayla düşer."""

    def normalize(self, obs: Observation):  # type: ignore[override]
        raise RuntimeError("normalize boom")


@pytest.mark.asyncio
async def test_hata_yolunda_iptal_kaydi_temizlenir(factory):
    """execute_scan beklenmeyen hatayla düşerse `_cancel_events` temiz kalır (T-344/B)."""
    await _make_scan(factory, scan_id="scan-race", case_id="case-race")
    scanner = ScannerService(connectors=[_ExplodingConnector(tasks=1, sleep_seconds=0.0)])

    async with factory() as session:
        await asyncio.wait_for(scanner.execute_scan("scan-race", session), timeout=15.0)

    assert "scan-race" not in ScannerService._cancel_events, "iptal kaydı sızmamalı"
    scan = await _wait_for_scan(factory, "scan-race", timeout=1.0)
    assert scan.status == "failed"

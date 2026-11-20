"""R-01/R-08/R-04: planner çağrısı moda bağlı, sessiz kalmaz, öncelik uygulanır.

* **R-01**: O1'de `planner_cap` 0 olduğu için `QueryPlanner` hiç çağrılmamalı;
  aksi halde kullanılmayacak bir LLM isteğinin gecikmesi + bütçesinden 1 çağrı
  ödenirdi. O2'de çağrı yapılır.
* **R-08**: plan üretilip hiçbir sorgu görev olmazsa (tekrar/filtre) log yayınlanır.
* **R-04**: `priority` DB'de kalıcı; düşük değer önce koşar (bütçe kesintisinde
  planner görevleri ilk atlanan olmaz).
"""

from __future__ import annotations

import asyncio

import pytest
from showis.db.base import Base
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.models import Task as DbTask
from showis.services.orchestrator import event_broadcaster
from showis.services.scanner import ScannerService
from showis_contracts import (
    Platform,
    QueryPlan,
    RiskTier,
    ScanContext,
    SearchQuery,
    TaskSpec,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

SCAN_TIMEOUT_S = 20.0


# ---------------------------------------------------------------------------
# Sahte parçalar
# ---------------------------------------------------------------------------


class StubConnector:
    """Planlama sayısı ve kimliği ayarlanabilir, ağa çıkmayan connector."""

    platform: Platform = Platform.GITHUB
    tier: RiskTier = RiskTier.A

    def __init__(self, name: str, tasks: int = 0, query: str | None = None):
        self.name = name
        self.task_count = tasks
        self.query = query
        self.ran: list[str] = []

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        specs: list[TaskSpec] = []
        for i in range(self.task_count):
            params = {"i": i}
            if self.query is not None:
                params["query"] = self.query
            specs.append(
                TaskSpec(
                    id=f"{self.name}-{i}",
                    connector=self.name,
                    platform=self.platform,
                    kind="search",
                    params=params,
                )
            )
        return specs

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list:
        self.ran.append(task.id)
        return []

    def normalize(self, obs) -> tuple[list, list]:
        return [], []


class RecordingLLM:
    """`complete` çağrılarını sayan sahte LLM istemcisi."""

    def __init__(self, queries: list[SearchQuery] | None = None):
        self.calls = 0
        self.queries = (
            queries
            if queries is not None
            else [
                SearchQuery(
                    query=f'"kerem{i}" site:github.com',
                    platform=Platform.GITHUB,
                    priority=1,
                )
                for i in range(3)
            ]
        )

    async def complete(self, messages, schema=None, temperature=None, **kwargs):
        self.calls += 1
        if schema is QueryPlan:
            return QueryPlan(queries=self.queries)
        return "açıklama"


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
    session_factory: async_sessionmaker[AsyncSession],
    *,
    mode: str = "O2",
    budget: dict | None = None,
    scan_id: str = "scan-planner",
    case_id: str = "case-planner",
) -> str:
    async with session_factory() as session:
        session.add_all(
            [
                DbCase(
                    id=case_id,
                    name="Planner",
                    purpose="test",
                    legal_basis="self_check",
                    retention_days=7,
                ),
                DbTarget(case_id=case_id, first_name="Ada", last_name="Lovelace", hints={}),
                DbScan(
                    id=scan_id, case_id=case_id, mode=mode, status="queued", budget=budget or {}
                ),
            ]
        )
        await session.commit()
    return scan_id


async def _run(
    scanner: ScannerService,
    session_factory: async_sessionmaker[AsyncSession],
    scan_id: str,
) -> None:
    async with session_factory() as session:
        await asyncio.wait_for(
            scanner.execute_scan(scan_id, session), timeout=SCAN_TIMEOUT_S
        )


async def _tasks(
    session_factory: async_sessionmaker[AsyncSession], scan_id: str
) -> list[DbTask]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(DbTask).where(DbTask.scan_id == scan_id).order_by(DbTask.id)
            )
        ).scalars().all()
        return list(rows)


async def _collect_events(scan_id: str, coro) -> list[dict]:
    queue = event_broadcaster.subscribe(scan_id)
    try:
        await coro
        events: list[dict] = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events
    finally:
        event_broadcaster.unsubscribe(scan_id, queue)


# ---------------------------------------------------------------------------
# R-01: O1'de planner LLM'i hiç çağrılmaz
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_o1_planner_llm_cagirmaz(session_factory, monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    scan_id = await _make_scan(session_factory, mode="O1")
    llm = RecordingLLM()
    sitecheck = StubConnector("websearch", tasks=2)
    scanner = ScannerService(connectors=[sitecheck], llm_client=llm)

    await _run(scanner, session_factory, scan_id)

    assert llm.calls == 0, "O1'de planner LLM'i çağrılmamalı (tavan 0)"
    rows = await _tasks(session_factory, scan_id)
    # Planner görevleri `source="planner"` ile işaretlenir; connector görevleri değil.
    assert not [r for r in rows if (r.params or {}).get("source") == "planner"]
    # Connector görevleri etkilenmez
    assert sitecheck.ran == ["websearch-0", "websearch-1"]


@pytest.mark.asyncio
async def test_o2_planner_llm_cagirir_ve_gorev_uretir(session_factory, monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    scan_id = await _make_scan(session_factory, mode="O2")
    llm = RecordingLLM()
    scanner = ScannerService(connectors=[StubConnector("websearch")], llm_client=llm)

    await _run(scanner, session_factory, scan_id)

    assert llm.calls == 1
    planner_rows = [
        r for r in await _tasks(session_factory, scan_id) if r.connector == "websearch"
    ]
    assert len(planner_rows) == 3
    assert {r.priority for r in planner_rows} == {1}


# ---------------------------------------------------------------------------
# R-08: plan üretildi ama 0 görev eklendi -> sessiz kalma
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_sifir_gorev_uretirse_log_yayinlanir(session_factory, monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    scan_id = await _make_scan(session_factory, mode="O2")
    # Connector ile plan AYNI sorguyu üretir -> tekilleştirme hepsini eler.
    connector = StubConnector("websearch", tasks=1, query='"dup" site:github.com')
    llm = RecordingLLM(
        queries=[
            SearchQuery(
                query='"dup" site:github.com', platform=Platform.GITHUB, priority=1
            )
        ]
    )
    scanner = ScannerService(connectors=[connector], llm_client=llm)

    events = await _collect_events(
        scan_id, _run(scanner, session_factory, scan_id)
    )

    messages = [e["data"]["message"] for e in events if e.get("type") == "log"]
    assert any("0 yeni arama görevi" in m for m in messages), messages


# ---------------------------------------------------------------------------
# R-04: priority DB'de kalıcı ve çalışma sırasını belirler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dusuk_priority_once_kosar(session_factory, monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_CONCURRENCY", raising=False)
    scan_id = await _make_scan(session_factory, mode="O2", budget={"max_tasks": 1})
    planner_conn = StubConnector("websearch", tasks=0)
    slow = StubConnector("slowconn", tasks=1)  # priority 5 (varsayılan)
    llm = RecordingLLM(
        queries=[
            SearchQuery(
                query='"once" site:github.com', platform=Platform.GITHUB, priority=1
            )
        ]
    )
    scanner = ScannerService(connectors=[planner_conn, slow], llm_client=llm)

    await _run(scanner, session_factory, scan_id)

    rows = {r.connector: r for r in await _tasks(session_factory, scan_id)}
    assert rows["websearch"].status == "done", "priority=1 önce koşmalı"
    assert rows["slowconn"].status == "skipped"
    assert rows["slowconn"].error == "budget_or_timeout"

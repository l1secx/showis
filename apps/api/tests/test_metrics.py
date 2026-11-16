"""T-329: `/metrics` ve `/healthz` testleri."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from showis.api.v1 import ops
from showis.db.base import Base
from showis.db.models import Edge, Entity, Finding, Scan, Task
from showis.db.session import get_db
from showis.main import app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# --- fixture'lar -----------------------------------------------------------


@pytest.fixture
async def session_factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = f"sqlite+aiosqlite:///{tmp_path}/metrics.db"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


async def _seed(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    case_id = "case-1"
    for status, minutes in (("completed", 60), ("completed", 120), ("failed", 30)):
        db.add(
            Scan(
                id=f"scan-{status}-{minutes}",
                case_id=case_id,
                status=status,
                started_at=now - timedelta(minutes=minutes + 5),
                finished_at=now - timedelta(minutes=minutes),
                created_at=now - timedelta(minutes=minutes + 10),
            )
        )
    for status, n in (("done", 5), ("failed", 2), ("pending", 3), ("skipped", 1)):
        for i in range(n):
            db.add(
                Task(
                    id=f"task-{status}-{i}",
                    scan_id="scan-completed-60",
                    connector="c",
                    platform="p",
                    kind="k",
                    status=status,
                )
            )
    for status, n in (("confirmed", 4), ("candidate", 2), ("rejected", 1)):
        for i in range(n):
            db.add(
                Finding(
                    id=f"finding-{status}-{i}",
                    case_id=case_id,
                    scan_id="scan-completed-60",
                    status=status,
                    score=0.9,
                    entity_id="entity-0",
                    platform="github",
                    label=f"F{i}",
                )
            )
    for i in range(3):
        db.add(
            Entity(
                id=f"entity-{i}",
                case_id=case_id,
                type="person",
                platform="github",
                label=f"E{i}",
            )
        )
    db.add(
        Edge(
            id="edge-1",
            case_id=case_id,
            type="same_person",
            source_entity_id="entity-0",
            target_entity_id="entity-1",
            confidence=0.9,
        )
    )
    await db.commit()


def _parse(body: str) -> dict[str, list[tuple[str, float]]]:
    """Metriği `{ad: [(etiket, değer)]}` sözlüğüne çevirir (biçim doğrulaması ile)."""
    series: dict[str, list[tuple[str, float]]] = {}
    declared: dict[str, str] = {}
    for line in body.splitlines():
        if not line:
            continue
        assert line.startswith(("# HELP ", "# TYPE ", "showis_")), f"beklenmeyen satir: {line!r}"
        if line.startswith("# HELP "):
            name = line.split()[2]
            assert name.startswith("showis_"), name
            continue
        if line.startswith("# TYPE "):
            parts = line.split()
            assert len(parts) == 4, line
            declared[parts[2]] = parts[3]
            continue
        head, _, value = line.rpartition(" ")
        name = head.split("{")[0]
        series.setdefault(name, []).append((head, float(value)))
    # Her seri # TYPE ile bildirilmiş olmalı
    for name in series:
        base = name.removesuffix("_count").removesuffix("_sum").removesuffix("_max")
        assert name in declared or base in declared, f"TYPE eksik: {name}"
    assert declared, "hic # TYPE satiri yok"
    return series


def _value(series: dict[str, list[tuple[str, float]]], name: str, label: str) -> float:
    for head, value in series.get(name, []):
        if label in head:
            return value
    raise AssertionError(f"{name}{{{label}}} bulunamadi: {series.get(name)}")


# --- /metrics: biçim + sayımlar -------------------------------------------


async def test_metrics_format_and_counts(db_session: AsyncSession):
    await _seed(db_session)
    ops.reset_metrics_cache()

    counts = await ops._counts(db_session)
    durations = await ops._scan_durations(db_session)
    body = ops.render_metrics(counts, durations, budget_exceeded=7)

    series = _parse(body)  # biçim assert'leri burada
    assert _value(series, "showis_scans_total", 'status="completed"') == 2
    assert _value(series, "showis_scans_total", 'status="failed"') == 1
    assert _value(series, "showis_scans_total", 'status="cancelled"') == 0
    assert _value(series, "showis_tasks_total", 'status="done"') == 5
    assert _value(series, "showis_tasks_total", 'status="pending"') == 3
    assert _value(series, "showis_findings_total", 'status="confirmed"') == 4
    assert _value(series, "showis_findings_total", 'status="lead"') == 0
    assert _value(series, "showis_entities_total", "") == 3
    assert _value(series, "showis_edges_total", 'type="same_person"') == 1
    assert _value(series, "showis_edges_total", 'type="cross_link"') == 0
    assert _value(series, "showis_http_request_budget_exceeded_total", "") == 7
    assert body.rstrip().endswith(f'showis_app_info{{version="{ops.app_version()}"}} 1')


async def test_scan_duration_summary_uses_last_20(db_session: AsyncSession):
    await _seed(db_session)
    durations = await ops._scan_durations(db_session)
    # 2 tamamlanan tarama: 5 ve 5 dakika
    assert len(durations) == 2
    assert all(abs(d - 300.0) < 1 for d in durations)

    body = ops.render_metrics({}, durations, budget_exceeded=0)
    series = _parse(body)
    assert _value(series, "showis_scan_duration_seconds_count", "") == 2
    assert abs(_value(series, "showis_scan_duration_seconds_sum", "") - 600.0) < 2
    assert abs(_value(series, "showis_scan_duration_seconds_max", "") - 300.0) < 1


async def test_metrics_cache_avoids_repeated_queries(
    db_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
):
    await _seed(db_session)
    ops.reset_metrics_cache()
    calls = {"n": 0}
    original = ops._counts

    async def counting(session: AsyncSession) -> dict[tuple[str, str], int]:
        calls["n"] += 1
        return await original(session)

    ops._counts = counting  # type: ignore[assignment]
    try:
        transport = ASGITransport(app=app)
        async with session_factory() as req_session:
            app.dependency_overrides[get_db] = lambda: req_session
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r1 = await client.get("/api/v1/metrics", headers={"X-API-Token": "test-token"})
                r2 = await client.get("/api/v1/metrics", headers={"X-API-Token": "test-token"})
            app.dependency_overrides.clear()
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.text == r2.text
        assert calls["n"] == 1, "5 sn cache calismadi (ikinci istek DB'ye gitti)"
    finally:
        ops._counts = original  # type: ignore[assignment]


# --- /metrics: erişim ------------------------------------------------------


async def test_metrics_requires_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHOWIS_API_TOKEN", "test-token")
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    ops.reset_metrics_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/metrics")
    assert res.status_code == 401


async def test_healthz_is_public(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
):
    """T-330/F-04: /healthz kimliksiz erisilebilir (yalniz ok|degraded + check adlari)."""
    monkeypatch.setenv("SHOWIS_API_TOKEN", "test-token")
    monkeypatch.delenv("SHOWIS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("SHOWIS_HEALTHZ_VERBOSE", raising=False)

    async def fake_redis() -> dict[str, object]:
        return {"status": "ok"}

    monkeypatch.setattr(ops, "_check_redis", fake_redis)
    transport = ASGITransport(app=app)
    async with session_factory() as req_session:
        app.dependency_overrides[get_db] = lambda: req_session
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/healthz")
        app.dependency_overrides.clear()
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert set(data) == {"status", "checks"}, "kimliksiz yanit yalniz ozet donmeli"
    assert data["checks"] == {"db": "ok", "redis": "ok", "disk": "ok"}


# --- /healthz: sağlıklı / bozuk -------------------------------------------


async def test_healthz_ok(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_redis() -> dict[str, object]:
        return {"status": "ok"}

    monkeypatch.setattr(ops, "_check_redis", fake_redis)
    transport = ASGITransport(app=app)
    async with session_factory() as req_session:
        app.dependency_overrides[get_db] = lambda: req_session
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/healthz")
            verbose = await client.get("/healthz", headers={"X-Healthz-Verbose": "1"})
        app.dependency_overrides.clear()

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "checks": {"db": "ok", "redis": "ok", "disk": "ok"}}
    # verbose yanit bayragi env ile acilir; burada yalniz ayrintisiz hal dogrulanir
    assert verbose.status_code == 200


async def test_healthz_degraded_when_db_down(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
):
    class BrokenSession:
        async def execute(self, *_a, **_kw):
            raise RuntimeError("db down")

    async def fake_redis() -> dict[str, object]:
        return {"status": "ok"}

    monkeypatch.setattr(ops, "_check_redis", fake_redis)
    transport = ASGITransport(app=app)
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/healthz")
    finally:
        app.dependency_overrides.clear()

    assert res.status_code == 503
    data = res.json()
    assert data["status"] == "degraded"
    assert data["checks"]["db"] == "error"
    assert set(data) == {"status", "checks"}

"""E1 — scanner'in connector'lara `scan_mode` atamasi.

Neden gerekli: sitecheck plan asamasinda modu bilmeli ki dogru **istek
butcesini** (O2=600 / O3=1200) kullansin. `ScanContext` semasinda `mode` alani
yok; bu yuzden scanner modu connector niteligine yaziyor (`max_variant_tasks`
ile ayni desen).

Ag yok: connector sahte, plan yalnizca gordugu modu kaydeder.
"""

from __future__ import annotations

import uuid

import pytest
from showis.db.models import Case as DbCase
from showis.db.models import Scan as DbScan
from showis.db.models import Target as DbTarget
from showis.db.models import Task as DbTask
from showis.services.scanner import ScannerService
from showis_contracts import Observation, ObservationKind, Platform, RiskTier, TaskSpec
from sqlalchemy import select


class _ModeSpyConnector:
    """`scan_mode` niteligini okuyup gorev parametrelerine yazan sahte connector."""

    name = "modespy"
    platform = Platform.GITHUB
    tier = RiskTier.A
    supported_kinds = {"probe"}

    def __init__(self) -> None:
        self.scan_mode: str | None = None
        self.seen_mode: str | None = None

    async def plan(self, ctx):
        self.seen_mode = self.scan_mode
        return [
            TaskSpec(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind="probe",
                params={"mode": self.scan_mode or "?"},
            )
        ]

    async def run(self, task, ctx):
        return [
            Observation(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.PROFILE,
                subject="alice",
                tier=self.tier,
                data={},
            )
        ]

    def normalize(self, obs):
        return [], []


async def _run_scan(db_factory, connector: _ModeSpyConnector, mode: str) -> str:
    case_id = str(uuid.uuid4())
    scan_id = str(uuid.uuid4())
    async with db_factory() as session:
        session.add(
            DbCase(
                id=case_id,
                name="Mode Test",
                purpose="test",
                legal_basis="self_check",
                retention_days=7,
            )
        )
        session.add(
            DbTarget(
                case_id=case_id,
                first_name="Kerem",
                last_name="Boz",
                hints={},
                review_required=False,
            )
        )
        session.add(
            DbScan(id=scan_id, case_id=case_id, mode=mode, status="running", budget={})
        )
        await session.commit()

    async with db_factory() as session:
        await ScannerService(connectors=[connector]).execute_scan(scan_id, session)
    return scan_id


@pytest.mark.parametrize("mode", ["O2", "O3"])
async def test_scanner_connectora_scan_mode_atar(db_factory, mode):
    connector = _ModeSpyConnector()

    scan_id = await _run_scan(db_factory, connector, mode)

    assert connector.seen_mode == mode, "plan() scanner'in modunu gormeli"
    async with db_factory() as session:
        rows = (
            (
                await session.execute(
                    select(DbTask).where(
                        DbTask.scan_id == scan_id, DbTask.connector == "modespy"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows, "sahte connector gorev uretmeli"
    assert all(row.params.get("mode") == mode for row in rows)


async def test_scanner_sitecheck_butcesi_moda_gore_planlanir(db_factory, monkeypatch):
    """Uctan uca: O3 taramasinda sitecheck plani O3 butcesini (1200) kullanir."""
    from showis_connectors.sitecheck import wmn
    from showis_connectors.sitecheck.connector import SiteCheckConnector
    from showis_connectors.sitecheck.engine import UsernameCheckEngine

    class _NoHttp:
        user_agent = "test-ua"

        async def get(self, url, **kwargs):  # pragma: no cover - istek atilmamali
            raise AssertionError(f"testte ag istegi yok: {url}")

    connector = SiteCheckConnector(engine=UsernameCheckEngine(http_client=_NoHttp()))

    async def _noop_run(self, task, ctx):  # noqa: ANN001 - test stub (ag yok)
        return []

    # Plan gercek, run sahte: site limitleri plan asamasinda belirlenir.
    monkeypatch.setattr(SiteCheckConnector, "run", _noop_run)
    await _run_scan(db_factory, connector, "O3")

    async with db_factory() as session:
        rows = (
            (await session.execute(select(DbTask).where(DbTask.connector == "sitecheck")))
            .scalars()
            .all()
        )
    assert rows, "sitecheck gorev uretmeli"
    limits = {row.params.get("site_limit") for row in rows}
    assert len(limits) == 1, limits
    limit = limits.pop()
    n = len(rows)
    assert n <= 10
    assert limit == wmn.SITECHECK_REQUEST_BUDGET["O3"] // n
    assert n * limit <= wmn.SITECHECK_REQUEST_BUDGET["O3"]
    assert all(row.params.get("mode") == "O3" for row in rows)

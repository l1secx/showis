"""Site-check aile istek bütçesi: planlanan istek tarama bütçesini aşmaz."""

from __future__ import annotations

import pytest
from showis_connectors.sitecheck.budget import (
    ROBOTS_FACTOR,
    family_budget,
    primary_site_limit,
    search_site_limit,
)
from showis_connectors.sitecheck.connector import SiteCheckConnector
from showis_connectors.sitecheck.search_connector import SiteCheckSearchConnector
from showis_contracts import Platform, ScanContext, Seed, Target
from showis_engine.scan_modes import MODE_PROFILES


def _ctx(mode: str) -> ScanContext:
    seeds = [Seed(platform=Platform.WEB, username=f"user{i}") for i in range(10)]
    return ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(first_name="Ali", last_name="Veli"),
        seeds=seeds,
        mode=mode,
    )


@pytest.mark.parametrize("mode", ["O2", "O3"])
def test_family_total_within_scan_budget(mode):
    n = 10
    planned = n * (primary_site_limit(mode, n) + search_site_limit(mode, n)) * (1 + ROBOTS_FACTOR)
    assert planned <= family_budget(mode)
    assert family_budget(mode) <= MODE_PROFILES[mode]["max_http_requests"] / 2


def test_o1_plans_no_site_requests():
    assert primary_site_limit("O1", 6) == 0


def test_env_override_is_clamped(monkeypatch):
    monkeypatch.setenv("SHOWIS_SITECHECK_REQUEST_BUDGET", "999999")
    assert family_budget("O2") == MODE_PROFILES["O2"]["max_http_requests"] // 2


@pytest.mark.asyncio
async def test_plan_writes_site_limit_per_role():
    ctx = _ctx("O3")
    primary = await SiteCheckConnector().plan(ctx)
    search = await SiteCheckSearchConnector().plan(_ctx("O3").model_copy(update={"scan_id": "s2"}))
    assert primary and search
    assert {t.params["site_limit"] for t in primary} == {primary_site_limit("O3", len(primary))}
    assert {t.params["site_limit"] for t in search} == {search_site_limit("O3", len(search))}

"""Task 13: `QueryPlan` -> `TaskSpec` donusumu.

Planner'in urettigi sorgular gercek arama gorevlerine cevrilir: moda gore
tavanlanir, tekilleştirilir ve `finalize_query` kapisindan gecirilir.
"""

from __future__ import annotations

import pytest
from showis.services.scanner import PLANNER_SEARCH_LIMIT, ScannerService
from showis_contracts import Platform, QueryPlan, SearchQuery
from showis_engine.planner import finalize_query


def plan_with(queries: list[str]) -> QueryPlan:
    """Verilen sorgu metinlerinden bir `QueryPlan` kurar (platform/priority sabit)."""
    return QueryPlan(
        queries=[
            SearchQuery(query=text, platform=Platform.GITHUB, priority=2)
            for text in queries
        ]
    )


@pytest.fixture
def svc() -> ScannerService:
    """Connector/LLM gerektirmeyen servis ornegi (donusum saf)."""
    return ScannerService(connectors=[])


def test_planner_specs_respect_mode_cap(svc):
    plan = plan_with([f'"k{i}" site:github.com' for i in range(30)])
    assert len(svc._planner_task_specs(plan, "O2", [])) == 8
    assert svc._planner_task_specs(plan, "O1", []) == []


def test_planner_specs_dedupe_and_drop_empty(svc):
    plan = plan_with(
        [
            '"a" site:x.com',
            '"a" site:x.com',
            '"" site:y.com',
            # finalize_query PolicyViolationError -> sorgu atlanir
            'password site:z.com',
        ]
    )
    specs = svc._planner_task_specs(plan, "O3", [])
    assert [s.params["query"] for s in specs] == ['"a" site:x.com']


def test_planner_specs_skip_existing_connector_queries(svc):
    specs = svc._planner_task_specs(
        plan_with(['"a" site:x.com']), "O3", ['"a" site:x.com']
    )
    assert specs == []
    # Normalizasyon: bosluk ve buyuk/kucuk harf farki tekilleştirmeyi atlatmaz
    again = svc._planner_task_specs(
        plan_with(['"a" site:x.com']), "O3", ['  "A"   site:X.com ']
    )
    assert again == []


def test_planner_specs_are_marked_and_sanitized(svc):
    specs = svc._planner_task_specs(plan_with(['"a<b>" site:x.com']), "O2", [])
    assert specs[0].params["source"] == "planner"
    assert specs[0].params["query"] == finalize_query(specs[0].params["query"])
    # Gorev sozlesmesi: arama gorevi + moda bagli olmayan sabit limit
    assert specs[0].connector == "websearch"
    assert specs[0].kind == "search"
    assert specs[0].platform == Platform.GITHUB
    assert specs[0].params["limit"] == PLANNER_SEARCH_LIMIT

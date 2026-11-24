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
    assert len(svc._planner_task_specs(plan, "O2", [])) == 14
    assert svc._planner_task_specs(plan, "O1", []) == []


def test_planner_specs_cap_applies_after_filtering(svc):
    """Tavan **filtreden sonra** uygulanır: elenen ilk 3 girdi yer kaplamaz.

    Aksi halde `queries[:cap]` ile ilk elemanlar alinir, elenenler yer kaplar;
    oysa planda tavani dolduracak kadar gecerli sorgu daha var.
    """
    valid = [f'"v{i}" site:github.com' for i in range(20)]
    dropped = [
        valid[0],  # tekrar: asagida ayni sorgu geçerli olarak var
        '"" site:y.com',  # arama terimi yok
        "password site:z.com",  # finalize_query -> PolicyViolationError
    ]
    plan = plan_with(dropped + valid)

    specs = svc._planner_task_specs(plan, "O2", [])

    assert len(specs) == 14
    # Sirayla: ilk 14 gecerli sorgu (kalanlar tavana takilir)
    assert [s.params["query"] for s in specs] == valid[:14]


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


def test_planner_specs_o3_caps_at_thirty_two(svc):
    """O3 tavani 32: 40 tekil sorgudan tam 32 gorev uretilir."""
    plan = plan_with([f'"k{i}" site:github.com' for i in range(40)])
    specs = svc._planner_task_specs(plan, "O3", [])
    assert len(specs) == 32
    assert [s.params["query"] for s in specs] == [
        f'"k{i}" site:github.com' for i in range(32)
    ]


def test_planner_specs_clamp_priority(svc):
    """Plan priority'si 1..5 araligina kirpilir (9 -> 5, 0 -> 1)."""
    plan = QueryPlan(
        queries=[
            SearchQuery(query='"a" site:x.com', platform=Platform.GITHUB, priority=9),
            SearchQuery(query='"b" site:x.com', platform=Platform.GITHUB, priority=0),
        ]
    )
    specs = svc._planner_task_specs(plan, "O2", [])
    assert [s.priority for s in specs] == [5, 1]


def test_planner_specs_preserve_plan_order(svc):
    """Cok elemanli planda sira korunur: queries[i].query -> specs[i]."""
    queries = [f'"q{i}" site:github.com' for i in range(5)]
    specs = svc._planner_task_specs(plan_with(queries), "O3", [])
    assert [s.params["query"] for s in specs] == queries


def test_planner_specs_drop_single_quoted_empty_term(svc):
    """Tek tirnakli bos terim (`'' site:y.com`) arama terimi sayilmaz."""
    plan = plan_with(["'' site:y.com", '"a" site:x.com'])
    specs = svc._planner_task_specs(plan, "O3", [])
    assert [s.params["query"] for s in specs] == ['"a" site:x.com']


@pytest.mark.parametrize(
    "query",
    [
        "-site:x.com",  # negatif operator: yalniz `-` kalir
        "OR",  # tek basina boolean kelime
        "and",
        "NOT",
        "site:a.com site:b.com",  # yalniz operatorler
        "-",
        "()",
    ],
)
def test_planner_specs_drop_operator_leftovers(svc, query):
    """R-07: operator/noktalama artigi arama terimi degildir."""
    assert svc._planner_task_specs(plan_with([query]), "O3", []) == []


@pytest.mark.parametrize(
    "query",
    [
        '"kerem" site:x.com',
        "kerem OR boz site:x.com",  # gercek terim varken boolean kalabilir
        "site:x.com kerem",
        "-site:x.com kerem boz",
    ],
)
def test_planner_specs_keep_real_terms(svc, query):
    specs = svc._planner_task_specs(plan_with([query]), "O3", [])
    assert [s.params["query"] for s in specs] == [query]

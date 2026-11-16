import json

import pytest
import respx
from showis_contracts import Platform, QueryPlan, Target
from showis_engine.planner import QueryPlanner, build_template_fallback_queries
from showis_engine.variants import generate_variants
from showis_llm import LLMClient


def test_planner_fallback_templates():
    target = Target(first_name="Kemal", last_name="Aydın")
    variants = generate_variants("Kemal", "Aydın")

    plan = build_template_fallback_queries(target, variants)
    assert isinstance(plan, QueryPlan)
    assert len(plan.queries) > 0
    assert len(plan.queries) <= 50

    query_strings = [q.query for q in plan.queries]
    assert any("site:github.com" in q for q in query_strings)
    assert any("site:tryhackme.com" in q for q in query_strings)
    assert any("site:hackthebox.com" in q for q in query_strings)


@pytest.mark.asyncio
async def test_planner_with_llm():
    target = Target(first_name="Selin", last_name="Kara")
    variants = generate_variants("Selin", "Kara")

    llm = LLMClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-dummy",
        model="gpt-4o-mini",
        session_id="test",
    )
    planner = QueryPlanner(llm_client=llm)

    mock_plan = {
        "queries": [
            {
                "query": '"Selin Kara" cybersecurity',
                "platform": "web",
                "rationale": "Siber güvenlik alanında web araması",
                "priority": 1,
            }
        ]
    }

    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": json.dumps(mock_plan)}}]
            },
        )

        plan = await planner.plan(target, variants)
        assert len(plan.queries) == 1
        assert plan.queries[0].platform == Platform.WEB
        assert plan.queries[0].query == '"Selin Kara" cybersecurity'


def test_variant_terms_are_unique():
    """Uzun OR listesi tekrarsiz olmali (sorgu uzunlugu + gurultu)."""
    from showis_engine.planner import _VARIANT_OR_TERMS

    lowered = [t.lower() for t in _VARIANT_OR_TERMS]
    assert len(lowered) == len(set(lowered))
    assert len(_VARIANT_OR_TERMS) > 10


@pytest.mark.asyncio
async def test_planner_rejects_forbidden_llm_query():
    """LLM yasak desen (leak/breach) uretirse sablon fallback'e donulmeli."""
    target = Target(first_name="Selin", last_name="Kara")
    variants = generate_variants("Selin", "Kara")
    llm = LLMClient(
        base_url="https://api.openai.com/v1",
        api_key="sk-dummy",
        model="gpt-4o-mini",
        session_id="test",
    )
    planner = QueryPlanner(llm_client=llm)
    bad_plan = {
        "queries": [
            {
                "query": '"Selin Kara" breach dump password',
                "platform": "web",
                "rationale": "x",
                "priority": 1,
            }
        ]
    }
    with respx.mock:
        respx.post("https://api.openai.com/v1/chat/completions").respond(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": json.dumps(bad_plan)}}
                ]
            },
        )
        plan = await planner.plan(target, variants)
    assert all("breach" not in q.query for q in plan.queries)
    assert any("site:github.com" in q.query for q in plan.queries)

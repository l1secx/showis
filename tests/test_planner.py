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


# ---------------------------------------------------------------------------
# T-377 (SEC-10): ipucu/sorgu enjeksiyonu — tek kaynak sanitizasyon
# ---------------------------------------------------------------------------


def test_normal_turkce_ipucu_bozulmaz():
    from showis_engine.planner import sanitize_hint

    assert sanitize_hint("ODTÜ") == "ODTÜ"
    assert sanitize_hint("İstanbul") == "İstanbul"
    assert sanitize_hint("  Orta Doğu Teknik Üniversitesi ") == "Orta Doğu Teknik Üniversitesi"


def test_operator_enjeksiyonu_notralize_edilir():
    from showis_engine.planner import sanitize_hint

    dirty = 'x site:evil.tld filetype:pdf inurl:admin intitle:"index of"'
    cleaned = sanitize_hint(dirty)
    assert "site:" not in cleaned
    assert "filetype:" not in cleaned
    assert "inurl:" not in cleaned
    assert "intitle:" not in cleaned
    assert cleaned.startswith("x site_evil.tld")


def test_boolean_ve_negasyon_notralize_edilir():
    from showis_engine.planner import sanitize_hint

    cleaned = sanitize_hint('Ahmet OR -Veli NOT "Yılmaz"')
    assert " OR " not in cleaned
    assert " NOT " not in cleaned
    assert not cleaned.split()[1].startswith("-")
    assert '"' not in cleaned


def test_uzunluk_kirpilir():
    from showis_engine.planner import (
        MAX_HINT_LENGTH,
        MAX_QUERY_LENGTH,
        sanitize_hint,
        sanitize_query,
    )

    long_hint = "a" * 500
    assert len(sanitize_hint(long_hint)) == MAX_HINT_LENGTH
    assert len(sanitize_query('"' + "a" * 500 + '"')) == MAX_QUERY_LENGTH


def test_kontrol_karakteri_reddedilir():
    from showis_engine.planner import sanitize_hint, sanitize_query

    assert sanitize_hint("ok\x00bad") == ""
    assert sanitize_hint("satir\nsonu") == ""
    assert sanitize_query("a\x1fb") == ""


def test_bos_hint_bos_doner():
    from showis_engine.planner import sanitize_hint

    assert sanitize_hint(None) == ""
    assert sanitize_hint("") == ""
    assert sanitize_hint("   ") == ""


def test_finalize_query_yasak_deseni_reddeder():
    from showis_engine.planner import finalize_query
    from showis_engine.policy import PolicyViolationError

    with pytest.raises(PolicyViolationError):
        finalize_query("site:pastebin.com sızıntı")
    with pytest.raises(ValueError):
        finalize_query("")
    # Mesru site: operatoru korunur (sistem sorgusu)
    assert finalize_query('"Ahmet Yılmaz" site:github.com') == '"Ahmet Yılmaz" site:github.com'


@pytest.mark.asyncio
async def test_websearch_connector_ipucunu_sanitize_eder():
    from showis_connectors.websearch.connector import WebSearchConnector
    from showis_contracts import ScanContext, Target

    connector = WebSearchConnector(search_provider=None)
    ctx = ScanContext(
        case_id="c",
        scan_id="s",
        target=Target(
            first_name="Ahmet",
            last_name="Yılmaz",
            hints={"city": "İstanbul site:evil.tld", "company": 'ACME "OR" 1=1'},
        ),
    )
    queries = connector._queries(ctx)
    joined = " | ".join(queries)
    assert "site:" not in joined
    assert '"OR"' not in joined
    assert "İstanbul" in joined  # okunur kısım korunur


@pytest.mark.asyncio
async def test_github_connector_ipucunu_sanitize_eder():
    from showis_connectors.github import GitHubConnector
    from showis_contracts import Platform, ScanContext, Target

    connector = GitHubConnector(token=None)
    ctx = ScanContext(
        case_id="c",
        scan_id="s",
        target=Target(
            first_name="Ahmet",
            last_name="Yılmaz",
            hints={"city": 'İzmir" in:readme site:evil.tld', "company": "ACME"},
        ),
    )
    tasks = await connector.plan(ctx)
    name_task = next(t for t in tasks if t.params.get("query", "").find("in:name") >= 0)
    query = str(name_task.params["query"])
    assert 'in:readme' not in query
    assert "site:" not in query
    assert "location:\"İzmir" in query
    assert isinstance(name_task.platform, Platform)

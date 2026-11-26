import json

import pytest
import respx
from showis_contracts import Platform, QueryPlan, SearchQuery, Target
from showis_engine.planner import QueryPlanner, build_template_fallback_queries
from showis_engine.variants import generate_variants
from showis_llm import LLMClient


def test_planner_fallback_templates():
    target = Target(first_name="Kemal", last_name="Aydın")
    variants = generate_variants("Kemal", "Aydın")

    plan = build_template_fallback_queries(target, variants)
    assert isinstance(plan, QueryPlan)
    assert len(plan.queries) > 0
    # Site tablosu 50 ile tavanlanır; varyant/isim/CTF sorguları tavan dışında
    # eklenir (eski kod tüm listeyi 50'ye kırpıyordu).
    assert len(plan.queries) <= 80

    query_strings = [q.query for q in plan.queries]
    assert any("site:github.com" in q for q in query_strings)
    assert any("site:tryhackme.com" in q for q in query_strings)
    assert any("site:hackthebox.com" in q for q in query_strings)


def test_planner_fallback_varyant_ve_profil_sorgulari_korunur():
    """Tavan yalnız site bloğuna uygulanır; varyant/THM/HTB/generic korunur."""
    target = Target(first_name="Kemal", last_name="Aydın")
    variants = generate_variants("Kemal", "Aydın")
    plan = build_template_fallback_queries(target, variants)
    rationales = [q.rationale for q in plan.queries]
    assert any("Varyant" in r for r in rationales)
    assert any("TryHackMe profil" in r for r in rationales)
    assert any("Birleşik ad soyad" in r for r in rationales)
    assert any("Web genelinde isim" in r for r in rationales)


def test_handle_mode_varyant_sorgulari_da_plana_girer():
    target = Target(kind="handle", nickname="kboz")
    variants = generate_variants("", "", {}, nickname="kboz")
    plan = build_template_fallback_queries(target, variants)
    assert any("Varyant" in q.rationale for q in plan.queries)
    assert all("kboz" in q.query for q in plan.queries)


def test_planner_fallback_includes_pool_identifiers():
    target = Target(kind="name", first_name="Kemal", last_name="Aydın")
    plan = build_template_fallback_queries(
        target,
        generate_variants("Kemal", "Aydın"),
        [{"kind": "domain", "value": "acme.example"}, {"kind": "email", "value": "k@acme.example"}],
    )
    queries = " ".join(q.query for q in plan.queries)
    assert "acme.example" in queries
    assert "k@acme.example" in queries or "k_acme.example" in queries


def test_handle_mode_keeps_pool_queries():
    target = handle_target("keremboz")
    plan = build_template_fallback_queries(
        target, None, [{"kind": "organization", "value": "Acme Lojistik"}]
    )
    queries = " ".join(q.query for q in plan.queries)
    assert "Acme Lojistik" in queries
    assert all("keremboz" in q.query or q.rationale.startswith("Havuz kaydı") for q in plan.queries)


def test_planner_event_query_uses_place_and_organization():
    target = name_target("Kemal", "Aydın")
    plan = build_template_fallback_queries(
        target,
        None,
        [
            {
                "kind": "event",
                "value": "Konferans konuşması",
                "attributes": {"place": "Ankara", "organization": "Acme"},
            }
        ],
    )
    event_queries = [q for q in plan.queries if "Konferans" in q.query]
    assert event_queries
    assert any("Ankara" in q.query and "Acme" in q.query for q in event_queries)


# ---------------------------------------------------------------------------
# Faz 1 = A: handle-farkında planlama
# ---------------------------------------------------------------------------


def name_target(first: str, last: str, nickname: str = "") -> Target:
    return Target(kind="name", first_name=first, last_name=last, nickname=nickname)


def handle_target(nickname: str) -> Target:
    return Target(kind="handle", nickname=nickname)


def _site_operands(plan: QueryPlan) -> set[str]:
    """`"{terim}" site:...` biçimindeki sorguların site operandlarını toplar."""
    operands: set[str] = set()
    for q in plan.queries:
        _, _, rest = q.query.partition('" ')
        if rest.startswith("site:"):
            operands.add(rest)
    return operands


def test_handle_mode_plans_nick_queries_only():
    plan = build_template_fallback_queries(
        handle_target("kerem"), generate_variants("", "", {}, nickname="kerem")
    )
    queries = [q.query for q in plan.queries]
    assert queries and all("kerem" in q for q in queries)
    assert not any('""' in q for q in queries)
    assert not any("Kerem" in q for q in queries)


def test_handle_mode_kapsami_isim_yoluyla_ayni():
    name_plan = build_template_fallback_queries(name_target("Kerem", "Boz"), None)
    handle_plan = build_template_fallback_queries(handle_target("kerem"), None)

    assert _site_operands(handle_plan) == _site_operands(name_plan)
    handle_queries = [q.query for q in handle_plan.queries]
    assert '"kerem" site:github.com' in handle_queries
    assert '"kerem" site:gitlab.com' in handle_queries
    # platform-bağımsız sorgu da üretilir
    assert '"kerem"' in handle_queries
    assert all(q.rationale for q in handle_plan.queries)
    assert all(q.priority for q in handle_plan.queries)


def test_name_mode_plan_is_unchanged():
    plan = build_template_fallback_queries(name_target("Kerem", "Boz"), None)
    assert plan.queries[0].query == '"Kerem Boz" site:github.com'


def test_name_mode_nick_sorgulari_sonuna_eklenir():
    base = build_template_fallback_queries(name_target("Kerem", "Boz"), None)
    with_nick = build_template_fallback_queries(name_target("Kerem", "Boz", "kb"), None)

    base_queries = [q.query for q in base.queries]
    assert [q.query for q in with_nick.queries[: len(base_queries)]] == base_queries
    tail = [q.query for q in with_nick.queries[len(base_queries) :]]
    assert '"kb" site:github.com' in tail
    assert '"kb"' in tail
    assert not any('""' in q for q in with_nick.queries)


def test_template_queries_pass_finalize_query():
    from showis_engine.planner import finalize_query

    plan = build_template_fallback_queries(handle_target("kerem"), None)
    assert all(q.query == finalize_query(q.query) for q in plan.queries)


def test_template_gate_isim_yolunda_da_uygulanir():
    from showis_engine.planner import finalize_query

    plan = build_template_fallback_queries(
        name_target("Kerem", "Boz", "kb"),
        generate_variants("Kerem", "Boz", nickname="kb"),
    )
    assert all(q.query == finalize_query(q.query) for q in plan.queries)


def test_nicksiz_handle_sorgu_uretmez():
    """Nick normalize edilemiyorsa bozuk sorgu değil, hiç sorgu üretilmez."""
    for target in (handle_target(""), handle_target("@"), handle_target('"')):
        assert build_template_fallback_queries(target, None).queries == []


def test_yasak_desene_takilan_sablon_sorgulari_atlanir():
    """finalize_query fırlatırsa o sorgu plana girmez (tüm şablon yolu)."""
    assert build_template_fallback_queries(handle_target("leak"), None).queries == []


# --- Handle garantisi kod düzeyinde: nick dışı sorgu plana giremez ---


def test_handle_mode_gerekcesinde_tam_isim_gecmez():
    """Nick gerekçesi "tam isim" → "nick" değişiminden üretilir; bağ pinlenir."""
    plan = build_template_fallback_queries(handle_target("kerem"), None)
    assert plan.queries
    assert not any("tam isim" in q.rationale for q in plan.queries)


def test_handle_kind_isim_alanlari_dolu_olsa_da_nick_sorgusu_uretir():
    target = Target(kind="handle", first_name="Kerem", last_name="Boz", nickname="kb")
    queries = [q.query for q in build_template_fallback_queries(target, None).queries]
    assert queries and all("kb" in q for q in queries)
    assert not any("Kerem Boz" in q for q in queries)


def test_isim_kind_ama_ad_soyad_bos_nick_sorgusu_uretir():
    target = Target(kind="name", nickname="kb")
    queries = [q.query for q in build_template_fallback_queries(target, None).queries]
    assert queries and all("kb" in q for q in queries)
    assert not any('""' in q for q in queries)


def test_handle_filtresi_nick_disi_sorgulari_eler():
    from showis_engine.planner import _enforce_handle_queries

    queries = [
        SearchQuery(query='"Kerem Boz" site:github.com', platform=Platform.GITHUB),
        SearchQuery(query='"kb" site:github.com', platform=Platform.GITHUB),
        SearchQuery(query='"KB" github', platform=Platform.WEB),
    ]
    kept = [q.query for q in _enforce_handle_queries(handle_target("kb"), queries)]
    # büyük/küçük harf duyarsız, alt dize eşleşmesi
    assert kept == ['"kb" site:github.com', '"KB" github']


def test_handle_filtresi_bos_nickte_plani_bosaltir():
    from showis_engine.planner import _enforce_handle_queries

    queries = [SearchQuery(query='"kb" site:github.com', platform=Platform.GITHUB)]
    assert _enforce_handle_queries(handle_target("   "), queries) == []


def test_isim_modunda_filtre_dokunmaz():
    from showis_engine.planner import _enforce_handle_queries

    plan = build_template_fallback_queries(name_target("Kerem", "Boz"), None)
    kept = _enforce_handle_queries(name_target("Kerem", "Boz"), plan.queries)
    assert [q.query for q in kept] == [q.query for q in plan.queries]
    assert len(kept) == len(plan.queries)
    assert kept[0].query == '"Kerem Boz" site:github.com'


def test_prompt_handle_kuralini_tasir():
    from showis_llm import render_prompt

    rendered = render_prompt(
        "query_planner",
        first_name="",
        last_name="",
        hints={},
        variants=["kerem"],
        kind="handle",
        nickname="kerem",
    )
    assert "handle" in rendered
    assert "kerem" in rendered
    assert "uydurma" in rendered


@pytest.mark.asyncio
async def test_planner_llm_promptuna_kind_ve_nickname_koyar():
    target = handle_target("kerem")
    variants = generate_variants("", "", {}, nickname="kerem")
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
                "query": '"kerem" site:github.com',
                "platform": "github",
                "rationale": "Nick araması",
                "priority": 1,
            }
        ]
    }

    with respx.mock(assert_all_called=True) as mock:
        endpoint = mock.post("https://api.openai.com/v1/chat/completions").respond(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": json.dumps(mock_plan)}}]
            },
        )
        plan = await planner.plan(target, variants)

    prompt = json.loads(endpoint.calls[0].request.content)["messages"][0]["content"]
    assert "handle" in prompt
    assert "kerem" in prompt
    assert plan.queries[0].query == '"kerem" site:github.com'


@pytest.mark.asyncio
async def test_llm_handle_planinda_isim_sorgusu_elenir():
    """LLM isim uydursa bile handle planına giremez (kod düzeyi garanti)."""
    target = handle_target("kb")
    variants = generate_variants("", "", {}, nickname="kb")
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
                "query": '"Kerem Boz" site:github.com',
                "platform": "github",
                "rationale": "Uydurulmuş tam isim",
                "priority": 1,
            },
            {
                "query": '"kb" site:github.com',
                "platform": "github",
                "rationale": "Nick araması",
                "priority": 1,
            },
            {
                "query": '"kb" site:gitlab.com',
                "platform": "gitlab",
                "rationale": "Nick araması",
                "priority": 1,
            },
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

    queries = [q.query for q in plan.queries]
    assert queries == ['"kb" site:github.com', '"kb" site:gitlab.com']
    assert not any("Kerem Boz" in q for q in queries)
    assert planner.llm_disabled is False


@pytest.mark.asyncio
async def test_llm_bos_nick_handle_modunda_plan_bos_doner():
    target = handle_target("   ")
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
                "query": '"kb" site:github.com',
                "platform": "github",
                "rationale": "Nick araması",
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
        plan = await planner.plan(target, generate_variants("", ""))

    assert plan.queries == []
    assert planner.llm_disabled is False


@pytest.mark.asyncio
async def test_llm_kotu_sorgu_tum_plani_dusurmez():
    """Tek yasak sorgu atlanır, kalan sorgular yaşar; devre kesici açılmaz."""
    target = name_target("Selin", "Kara")
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
                "query": '"Selin Kara" breach dump password',
                "platform": "web",
                "rationale": "yasak",
                "priority": 1,
            },
            {
                "query": '"Selin Kara" site:github.com',
                "platform": "github",
                "rationale": "GitHub araması",
                "priority": 1,
            },
            {
                "query": '"Selin Kara" site:gitlab.com',
                "platform": "gitlab",
                "rationale": "GitLab araması",
                "priority": 1,
            },
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

    queries = [q.query for q in plan.queries]
    assert queries == ['"Selin Kara" site:github.com', '"Selin Kara" site:gitlab.com']
    assert all("breach" not in q for q in queries)
    assert planner.llm_disabled is False


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

import pytest
from showis_connectors.websearch import SearchHit, WebSearchConnector
from showis_contracts import Platform, RiskTier, ScanContext, Target


class FakeProvider:
    name = "fake"

    def __init__(self, hits):
        self.hits = hits
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 20, site: str | None = None):
        self.queries.append(query)
        return self.hits


def _ctx(hints=None) -> ScanContext:
    return ScanContext(
        case_id="c-1",
        scan_id="s-1",
        target=Target(first_name="Kerem", last_name="Boz", hints=hints or {}),
        seeds=[],
    )


def _hit(url: str, title: str, snippet: str = "", rank: int = 1) -> SearchHit:
    return SearchHit(url=url, title=title, snippet=snippet, rank=rank, provider="fake")


@pytest.mark.asyncio
async def test_plan_adds_school_query_and_is_tier_c():
    provider = FakeProvider([])
    conn = WebSearchConnector(search_provider=provider)

    tasks = await conn.plan(_ctx({"school": "ODTÜ", "city": "Ankara"}))

    queries = [t.params["query"] for t in tasks]
    assert queries[0] == '"Kerem Boz"'
    assert '"Kerem Boz" ODTÜ' in queries
    assert all(t.kind == "search" for t in tasks)
    assert conn.tier == RiskTier.C


@pytest.mark.asyncio
async def test_run_and_normalize_produce_name_scored_entities():
    provider = FakeProvider(
        [
            _hit("https://github.com/keremboz", "Kerem Boz · GitHub", "ODTÜ mezunu", 1),
            _hit("https://linkedin.com/in/keremboz", "Kerem Boz - LinkedIn", "", 2),
        ]
    )
    conn = WebSearchConnector(search_provider=provider)
    ctx = _ctx({"school": "ODTÜ"})

    task = (await conn.plan(ctx))[0]
    observations = await conn.run(task, ctx)

    assert len(observations) == 1
    assert len(observations[0].data["hits"]) == 2
    assert len(observations[0].evidence) == 2

    entities, edges = conn.normalize(observations[0])
    assert edges == []
    assert len(entities) == 2
    first = entities[0]
    assert first.platform == Platform.WEB
    assert first.attributes["name"] == "Kerem Boz · GitHub"
    assert first.attributes["detection"] == "websearch"
    assert first.evidence_ids  # bulgu kanitsiz gosterilmez


def test_normalize_is_stable_across_queries():
    conn = WebSearchConnector(search_provider=FakeProvider([]))
    from showis_contracts import Observation, ObservationKind

    def obs(query: str) -> Observation:
        return Observation(
            id=f"o-{query}",
            connector="websearch",
            platform=Platform.WEB,
            kind=ObservationKind.SEARCH_RESULT,
            subject=query,
            tier=RiskTier.C,
            data={"query": query, "hits": [{"url": "https://x.dev/kerem", "title": "Kerem Boz"}]},
        )

    a = conn.normalize(obs("q1"))[0][0]
    b = conn.normalize(obs("q2"))[0][0]
    assert a.id == b.id  # ayni URL -> ayni entity


@pytest.mark.asyncio
async def test_reklam_yonlendirme_url_leri_elenir():
    """google.com/goto gibi sarmalayici linkler bulguya donusmemeli."""
    provider = FakeProvider(
        [
            SearchHit(
                url="https://google.com/goto?url=CAESabc",
                title="kişiler içinde Kerem Boz",
                snippet="x",
                rank=1,
                provider="duckduckgo",
            ),
            SearchHit(
                url="https://tr.linkedin.com/in/kerem-boz",
                title="Kerem Boz | LinkedIn",
                snippet="y",
                rank=2,
                provider="duckduckgo",
            ),
        ]
    )
    conn = WebSearchConnector(search_provider=provider, evidence_store=None)
    ctx = _ctx()
    tasks = await conn.plan(ctx)
    observations = await conn.run(tasks[0], ctx)

    hits = observations[0].data["hits"]
    assert [h["url"] for h in hits] == ["https://tr.linkedin.com/in/kerem-boz"]
    entities, _ = conn.normalize(observations[0])
    assert [e.url for e in entities] == ["https://tr.linkedin.com/in/kerem-boz"]

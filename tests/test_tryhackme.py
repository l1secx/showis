"""TryHackMe connector testleri — sentetik fixture + mock browser/arama.

Fixture: tests/fixtures/thm/profile_keremboz.html (SENTETIK, gercek veri yok)
"""

from pathlib import Path

import pytest
import respx
from showis_connectors.tryhackme import TryHackMeConnector
from showis_connectors.websearch.base import SearchHit
from showis_contracts import (
    ObservationKind,
    Platform,
    ScanContext,
    Seed,
    Target,
)
from showis_engine.browser import BrowserClient

FIXTURE_HTML = (Path(__file__).parent / "fixtures/thm/profile_keremboz.html").read_text()
BROWSER_URL = "http://browser.test"
PROFILE_URL = "https://tryhackme.com/p/keremboz"


class MockSearchProvider:
    name = "mock"

    def __init__(self, hits: list[SearchHit] | None = None):
        self.hits = hits or []
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 20, site: str | None = None):
        self.queries.append(query)
        return self.hits[:limit]


def make_ctx(**kwargs) -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(first_name="Kerem", last_name="Boz", hints=kwargs.pop("hints", {})),
        seeds=kwargs.pop("seeds", []),
        **kwargs,
    )


def thm_render_mock(html: str = FIXTURE_HTML, blocked: bool = False, status: int = 200):
    return respx.post(f"{BROWSER_URL}/render").respond(
        json={
            "html": html,
            "screenshot_b64": None,
            "blocked": blocked,
            "status_code": status,
            "url": PROFILE_URL,
        }
    )


@pytest.mark.asyncio
async def test_thm_plan_with_seed_and_variants():
    connector = TryHackMeConnector(browser_client=BrowserClient(BROWSER_URL))
    ctx = make_ctx(
        seeds=[Seed(platform=Platform.TRYHACKME, username="keremboz")],
        hints={"nickname": "kerem"},
    )
    tasks = await connector.plan(ctx)
    kinds = [t.kind for t in tasks]
    assert "fetch_profile" in kinds
    usernames = [t.params.get("username") for t in tasks if t.kind == "fetch_profile"]
    assert "keremboz" in usernames
    # varyantlar da uretildi (nickname'den)
    assert any(u and u.startswith("kerem") for u in usernames)


@pytest.mark.asyncio
async def test_thm_fetch_profile_success():
    connector = TryHackMeConnector(
        browser_client=BrowserClient(BROWSER_URL, min_interval_per_host=0)
    )
    ctx = make_ctx()
    task = connector._make_task("fetch_profile", {"username": "keremboz"})

    with respx.mock:
        thm_render_mock()
        respx.get("https://tryhackme.com/avatar/keremboz.png").respond(
            content=b"\x89PNGavatarbytes"
        )
        obs = await connector.run(task, ctx)

    assert len(obs) == 1
    o = obs[0]
    assert o.kind == ObservationKind.PROFILE
    assert o.blocked is False
    assert o.data["username"] == "keremboz"
    assert o.data["rank"] == 1234
    assert o.data["streak"] == 12
    assert o.data["rooms_completed"] == 87
    assert any("github.com/keremboz" in link for link in o.data["links"])
    assert o.data["github_link"] == "https://github.com/keremboz"
    assert o.data["avatar_sha256"] is not None
    assert len(o.evidence) == 1
    assert o.evidence[0].kind == "html"

    # normalize -> Entity
    entities, edges = connector.normalize(o)
    assert len(entities) == 1
    ent = entities[0]
    assert ent.platform == Platform.TRYHACKME
    assert ent.label == "keremboz"
    assert ent.attributes["name"] == "keremboz"
    assert ent.attributes["rank"] == 1234
    # cross_link edge github icin uretilmeli
    assert any(e.type.value == "cross_link" for e in edges)


@pytest.mark.asyncio
async def test_thm_blocked_checkpoint_with_serp_fallback():
    provider = MockSearchProvider(
        hits=[
            SearchHit(
                url="https://tryhackme.com/p/keremboz",
                title="keremboz | TryHackMe",
                snippet="Rank #1234 profil ozeti",
                rank=1,
                provider="mock",
            )
        ]
    )
    connector = TryHackMeConnector(
        browser_client=BrowserClient(BROWSER_URL, min_interval_per_host=0),
        search_provider=provider,
    )
    ctx = make_ctx()
    task = connector._make_task("fetch_profile", {"username": "keremboz"})

    with respx.mock:
        thm_render_mock(blocked=True, status=429)
        obs = await connector.run(task, ctx)

    kinds = [o.kind for o in obs]
    assert ObservationKind.BLOCKED in kinds
    blocked_obs = next(o for o in obs if o.kind == ObservationKind.BLOCKED)
    assert blocked_obs.blocked is True
    # SERP fallback snippet uretildi
    snippets = [o for o in obs if o.kind == ObservationKind.SNIPPET]
    assert len(snippets) == 1
    assert "tryhackme.com/p/keremboz" in snippets[0].url
    assert provider.queries  # site: operatorlu sorgu calisti
    assert "site:tryhackme.com" in provider.queries[0]

    # blocked obs normalize'da entity uretmez
    entities, _ = connector.normalize(blocked_obs)
    assert entities == []


@pytest.mark.asyncio
async def test_thm_no_browser_client_error_observation():
    connector = TryHackMeConnector(browser_client=None)
    ctx = make_ctx()
    task = connector._make_task("fetch_profile", {"username": "keremboz"})
    obs = await connector.run(task, ctx)
    assert obs[0].error is not None
    assert "Browser" in obs[0].error or "browser" in obs[0].error.lower()


@pytest.mark.asyncio
async def test_thm_search_task_uses_provider_and_renders_hits():
    provider = MockSearchProvider(
        hits=[
            SearchHit(
                url="https://tryhackme.com/p/keremboz",
                title="keremboz",
                snippet="...",
                rank=1,
                provider="mock",
            )
        ]
    )
    connector = TryHackMeConnector(
        browser_client=BrowserClient(BROWSER_URL, min_interval_per_host=0),
        search_provider=provider,
    )
    ctx = make_ctx()
    task = connector._make_task(
        "search", {"query": '"Kerem Boz" site:tryhackme.com', "limit": 10}
    )
    with respx.mock:
        thm_render_mock()
        respx.get("https://tryhackme.com/avatar/keremboz.png").respond(content=b"x")
        obs = await connector.run(task, ctx)

    kinds = [o.kind for o in obs]
    assert ObservationKind.SEARCH_RESULT in kinds
    assert ObservationKind.PROFILE in kinds


@pytest.mark.asyncio
async def test_thm_identifier_from_url():
    connector = TryHackMeConnector()
    assert connector.identifier_from_url("https://tryhackme.com/p/foo.bar") == "foo.bar"
    assert connector.identifier_from_url("https://example.com/x") is None

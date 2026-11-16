"""HackTheBox connector testleri — sentetik fixture + mock browser/arama.

Fixture'lar: tests/fixtures/htb/ (SENTETIK, gercek veri yok)
Kritik: labs.hackthebox.com HIC cagrilmamalidir (H4) — test_forbidden_hosts.py
ayrica dogrular.
"""

from pathlib import Path

import pytest
import respx
from showis_connectors.hackthebox import HackTheBoxConnector
from showis_connectors.websearch.base import SearchHit
from showis_contracts import (
    ObservationKind,
    Platform,
    ScanContext,
    Seed,
    Target,
)
from showis_engine.browser import BrowserClient

FIXTURES = Path(__file__).parent / "fixtures/htb"
SHELL_HTML = (FIXTURES / "profile_shell.html").read_text()
RENDERED_HTML = (FIXTURES / "profile_rendered.html").read_text()
BROWSER_URL = "http://browser.test"
PROFILE_ID = "9f8e7d6c"
PROFILE_URL = f"https://app.hackthebox.com/profile/{PROFILE_ID}"


class MockSearchProvider:
    name = "mock"

    def __init__(self, hits: list[SearchHit] | None = None):
        self.hits = hits or []
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 20, site: str | None = None):
        self.queries.append(query)
        return self.hits[:limit]


def make_ctx(seeds: list[Seed] | None = None) -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(first_name="Kerem", last_name="Boz"),
        seeds=seeds or [],
    )


def htb_render_mock(html: str = RENDERED_HTML, url: str = PROFILE_URL):
    return respx.post(f"{BROWSER_URL}/render").respond(
        json={
            "html": html,
            "screenshot_b64": None,
            "blocked": False,
            "status_code": 200,
            "url": url,
        }
    )


@pytest.mark.asyncio
async def test_htb_identifier_from_url():
    connector = HackTheBoxConnector()
    assert connector.identifier_from_url("https://app.hackthebox.com/profile/abc-123") == "abc-123"
    assert connector.identifier_from_url("https://www.hackthebox.com/profile/abc123") == "abc123"
    assert connector.identifier_from_url("https://example.com/profile/x") is None


@pytest.mark.asyncio
async def test_htb_plan_seed_with_profile_url():
    connector = HackTheBoxConnector(
        browser_client=BrowserClient(BROWSER_URL), search_provider=MockSearchProvider()
    )
    ctx = make_ctx(
        seeds=[Seed(platform=Platform.HACKTHEBOX, url="https://www.hackthebox.com/profile/abc123")]
    )
    tasks = await connector.plan(ctx)
    fetch_tasks = [t for t in tasks if t.kind == "fetch_profile"]
    assert len(fetch_tasks) == 1
    assert fetch_tasks[0].params["profile_id"] == "abc123"
    # app. uzerinden render edilecek
    assert fetch_tasks[0].params["url"].startswith("https://app.hackthebox.com/profile/")


@pytest.mark.asyncio
async def test_htb_resolve_username_to_profile_id():
    provider = MockSearchProvider(
        hits=[
            SearchHit(
                url="https://www.hackthebox.com/profile/9f8e7d6c",
                title="keremboz - Hack The Box",
                snippet="HTB profile",
                rank=1,
                provider="mock",
            )
        ]
    )
    connector = HackTheBoxConnector(
        browser_client=BrowserClient(BROWSER_URL, min_interval_per_host=0),
        search_provider=provider,
    )
    ctx = make_ctx()
    task = connector._make_task("resolve", {"username": "keremboz"})

    with respx.mock:
        htb_render_mock()
        respx.get("https://account.hackthebox.com/storage/avatars/abc.png").respond(
            content=b"\x89PNGovatar"
        )
        obs = await connector.run(task, ctx)

    kinds = [o.kind for o in obs]
    assert ObservationKind.SEARCH_RESULT in kinds
    assert ObservationKind.PROFILE in kinds

    profile_obs = next(o for o in obs if o.kind == ObservationKind.PROFILE)
    assert profile_obs.data["profile_id"] == PROFILE_ID
    assert profile_obs.data["rank"] == "Pro"
    assert profile_obs.data["machines_completed"] == 42
    assert profile_obs.data["country"] == "Turkey"

    # provider'a dogru site: sorgusu gitti
    assert any("site:hackthebox.com/profile" in q for q in provider.queries)

    # normalize -> entity
    entities, _ = connector.normalize(profile_obs)
    assert len(entities) == 1
    ent = entities[0]
    assert ent.platform == Platform.HACKTHEBOX
    assert ent.attributes["machines_completed"] == 42
    assert ent.attributes["country"] == "Turkey"
    assert ent.attributes["github_link"] == "https://github.com/keremboz"


@pytest.mark.asyncio
async def test_htb_resolve_without_provider():
    connector = HackTheBoxConnector(browser_client=None, search_provider=None)
    ctx = make_ctx()
    task = connector._make_task("resolve", {"username": "keremboz"})
    obs = await connector.run(task, ctx)
    assert obs[0].error is not None
    assert "arama saglayicisi" in obs[0].error.lower()


@pytest.mark.asyncio
async def test_htb_spa_shell_detection():
    """JS'siz shell HTML'i profil verisi olarak yorumlanmamali."""
    connector = HackTheBoxConnector(
        browser_client=BrowserClient(BROWSER_URL, min_interval_per_host=0)
    )
    ctx = make_ctx()
    task = connector._make_task("fetch_profile", {"profile_id": PROFILE_ID})

    with respx.mock:
        htb_render_mock(html=SHELL_HTML)
        obs = await connector.run(task, ctx)

    assert len(obs) == 1
    o = obs[0]
    assert o.kind == ObservationKind.PROFILE
    assert o.data.get("spa_shell") is True


@pytest.mark.asyncio
async def test_htb_blocked_no_bypass():
    """Checkpoint/429 durumunda bypass denenmez; blocked observation uretilir."""
    connector = HackTheBoxConnector(
        browser_client=BrowserClient(BROWSER_URL, min_interval_per_host=0),
        search_provider=None,
    )
    ctx = make_ctx()
    task = connector._make_task("fetch_profile", {"profile_id": PROFILE_ID})

    with respx.mock:
        respx.post(f"{BROWSER_URL}/render").respond(
            json={
                "html": "<html>Vercel Security Checkpoint</html>",
                "screenshot_b64": None,
                "blocked": True,
                "status_code": 429,
                "url": PROFILE_URL,
            }
        )
        obs = await connector.run(task, ctx)

    assert obs[0].kind == ObservationKind.BLOCKED
    assert obs[0].blocked is True
    # provider yoksa fallback da yok
    assert len(obs) == 1

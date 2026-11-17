"""Site-check motoru testleri — sentetik SiteSpec + respx mock."""

import asyncio

import pytest
import respx
from showis_connectors.sitecheck.connector import SiteCheckConnector
from showis_connectors.sitecheck.engine import (
    ABSENT,
    ERROR,
    EXISTS,
    SKIPPED,
    UNKNOWN,
    UsernameCheckEngine,
)
from showis_connectors.sitecheck.search_connector import SiteCheckSearchConnector
from showis_connectors.sitecheck.sites import SiteSpec
from showis_contracts import (
    ObservationKind,
    Platform,
    ScanContext,
    Seed,
    Target,
)
from showis_engine.http import HttpClient

STATUS_SITE = SiteSpec(
    key="teststatus",
    name="TestStatus",
    platform=Platform.GITHUB,
    url_template="https://status.example.com/{u}",
    skip_robots=True,
)
JSON_SITE = SiteSpec(
    key="testjson",
    name="TestJson",
    platform=Platform.GITLAB,
    url_template="https://json.example.com/api?u={u}",
    json_path="data.name",
    skip_robots=True,
)
HTML_SITE = SiteSpec(
    key="testhtml",
    name="TestHtml",
    platform=Platform.REDDIT,
    url_template="https://html.example.com/{u}",
    absent_regex=r"user not found",
    skip_robots=True,
)


@pytest.fixture
def engine() -> UsernameCheckEngine:
    return UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False),
        sites=[STATUS_SITE, JSON_SITE, HTML_SITE],
        concurrency=3,
    )


@pytest.mark.asyncio
async def test_status_site_exists_and_absent(engine: UsernameCheckEngine):
    with respx.mock(assert_all_called=False) as router:
        router.get("https://status.example.com/alice").respond(200, text="ok")
        router.get("https://status.example.com/bob").respond(404)
        router.get("https://json.example.com/api").respond(
            200, json={"data": {"name": "x"}}
        )
        router.get("https://html.example.com/alice").respond(200, text="hello")
        router.get("https://html.example.com/bob").respond(
            200, text="user not found"
        )

        res_alice = await engine.check_username("alice")
        res_bob = await engine.check_username("bob")

    by_key_alice = {r.site.key: r for r in res_alice}
    by_key_bob = {r.site.key: r for r in res_bob}

    assert by_key_alice["teststatus"].status == EXISTS
    assert by_key_bob["teststatus"].status == ABSENT
    assert by_key_alice["testjson"].status == EXISTS
    assert by_key_bob["testjson"].status == EXISTS  # ayni mock -> var
    assert by_key_alice["testhtml"].status == EXISTS
    assert by_key_bob["testhtml"].status == ABSENT


@pytest.mark.asyncio
async def test_json_empty_list_is_absent(engine: UsernameCheckEngine):
    with respx.mock(assert_all_called=False) as router:
        router.get("https://status.example.com/u1").respond(404)
        router.get("https://json.example.com/api").respond(200, json=[])
        router.get("https://html.example.com/u1").respond(200, text="x")

        results = await engine.check_username("u1")

    by_key = {r.site.key: r for r in results}
    assert by_key["testjson"].status == ABSENT


@pytest.mark.asyncio
async def test_unknown_when_status_unexpected(engine: UsernameCheckEngine):
    with respx.mock(assert_all_called=False) as router:
        router.get("https://status.example.com/u2").respond(403)
        router.get("https://json.example.com/api").respond(200, json={"data": {}})
        router.get("https://html.example.com/u2").respond(500)

        results = await engine.check_username("u2")

    by_key = {r.site.key: r for r in results}
    # 403 status-sitesinde expect_status=200 disinda -> unknown
    assert by_key["teststatus"].status == UNKNOWN
    # json_path "data.name" -> {} uzerinde None -> absent
    assert by_key["testjson"].status == ABSENT
    # html sitesi: 500 -> HttpClient hata firlatir -> error
    assert by_key["testhtml"].status == ERROR


@pytest.mark.asyncio
async def test_robots_skip(engine: UsernameCheckEngine):
    site = SiteSpec(
        key="roboted",
        name="Roboted",
        platform=Platform.WEB,
        url_template="https://roboted.example.com/{u}",
        skip_robots=False,
    )
    eng = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False),
        sites=[site],
        concurrency=1,
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://roboted.example.com/robots.txt").respond(
            200, text="User-agent: *\nDisallow: /"
        )
        router.get("https://roboted.example.com/u").respond(200)
        results = await eng.check_username("u")
    assert results[0].status == SKIPPED


@pytest.mark.asyncio
async def test_connector_plan_and_normalize():
    sites = [STATUS_SITE, JSON_SITE, HTML_SITE]
    connector = SiteCheckConnector(
        engine=UsernameCheckEngine(
            http_client=HttpClient(enable_ssrf_guard=False),
            sites=sites,
            concurrency=3,
        ),
        sites=sites,
        max_usernames=2,
    )
    ctx = ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(first_name="Alice", last_name="Test"),
        seeds=[Seed(platform=Platform.GITHUB, username="alice")],
    )
    tasks = await connector.plan(ctx)
    assert any(t.params.get("username") == "alice" for t in tasks)

    with respx.mock(assert_all_called=False) as router:
        router.get("https://status.example.com/alice").respond(200, text="ok")
        router.get("https://json.example.com/api").respond(
            200, json={"data": {"name": "Alice"}}
        )
        router.get("https://html.example.com/alice").respond(200, text="hi")

        obs = await connector.run(tasks[0], ctx)

    assert len(obs) == 1
    assert obs[0].kind == ObservationKind.PROFILE
    hits = [c for c in obs[0].data["checks"] if c["status"] == EXISTS]
    assert len(hits) == 3

    entities, edges = connector.normalize(obs[0])
    platforms = {e.platform for e in entities}
    assert platforms == {Platform.GITHUB, Platform.GITLAB, Platform.REDDIT}
    assert all(e.attributes.get("username") == "alice" for e in entities)


@pytest.mark.asyncio
async def test_json_spec_error_body_is_unknown_not_exists():
    """403 + JSON hata govdesi 'var' sayilmamali (alan dolu olsa bile)."""
    site = SiteSpec(
        key="j403",
        name="J403",
        platform=Platform.GITLAB,
        url_template="https://j403.example.com/api?u={u}",
        json_path="data.name",
        expect_status=200,
        skip_robots=True,
    )
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False), sites=[site], concurrency=1
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://j403.example.com/api").respond(
            403, json={"data": {"name": "error"}}
        )
        results = await engine.check_username("alice")
    assert results[0].status == UNKNOWN


def test_json_site_specs_require_200():
    """_json_site ile tanimli specler 200 disi durumlari 'bilinmiyor' saymali."""
    from showis_connectors.sitecheck.sites import SITES

    json_specs = [s for s in SITES if s.json_path is not None]
    assert json_specs, "en az bir JSON spec bekleniyor"
    for spec in json_specs:
        assert spec.expect_status == 200, spec.key


# ---------------------------------------------------------------------------
# Handle (yalnız nick) hedefi
# ---------------------------------------------------------------------------


def _handle_ctx(nickname: str = "kerem", scan_id: str = "s-handle-sitecheck") -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id=scan_id,
        target=Target(kind="handle", nickname=nickname),
        seeds=[],
    )


def test_sitecheck_handle_mode_queries_nick():
    specs = asyncio.run(SiteCheckConnector().plan(_handle_ctx()))
    usernames = [str(s.params.get("username") or "") for s in specs]
    joined = " ".join(usernames + [str(s.params.get("query") or "") for s in specs])
    assert "kerem" in joined and '""' not in joined


def test_sitecheck_search_handle_mode_searches_nick():
    """Arama sorgusu nick'ten kurulur; boş tırnaklı sorgu atılmaz."""

    class _Provider:
        name = "fake"

        def __init__(self) -> None:
            self.queries: list[str] = []

        async def search(self, query: str, *, limit: int = 20, site: str | None = None):
            self.queries.append(query)
            return []

    provider = _Provider()
    connector = SiteCheckSearchConnector(search_provider=provider)
    asyncio.run(connector.plan(_handle_ctx(scan_id="s-handle-sitecheck-search")))
    assert provider.queries == ['"kerem"']


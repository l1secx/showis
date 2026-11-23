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
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
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
        router.get("https://status.example.com/alice").respond(200, text="alice profile")
        router.get("https://status.example.com/bob").respond(404)
        router.get("https://json.example.com/api").respond(
            200, json={"data": {"name": "x"}}
        )
        router.get("https://html.example.com/alice").respond(200, text="alice hello")
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
        router.get("https://status.example.com/alice").respond(200, text="alice profile")
        router.get("https://json.example.com/api").respond(
            200, json={"data": {"name": "Alice"}}
        )
        router.get("https://html.example.com/alice").respond(200, text="alice hi")

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



# ---------------------------------------------------------------------------
# Generic soft-404 / login duvarı yanlış-pozitif koruması
# ---------------------------------------------------------------------------


async def test_generic_200_without_username_is_unknown():
    site = SiteSpec(
        key="generic",
        name="Generic",
        platform=Platform.WEB,
        url_template="https://generic.example.com/{u}",
        skip_robots=True,
    )
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False), sites=[site], concurrency=1
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://generic.example.com/alice").respond(
            200, text="<html><title>Welcome</title>login page</html>"
        )
        results = await engine.check_username("alice")
    assert results[0].status == UNKNOWN
    assert "soft-404" in results[0].detail


async def test_soft404_title_is_absent():
    site = SiteSpec(
        key="soft404",
        name="Soft404",
        platform=Platform.WEB,
        url_template="https://soft404.example.com/{u}",
        skip_robots=True,
    )
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False), sites=[site], concurrency=1
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://soft404.example.com/alice").respond(
            200,
            text="<html><title>User not found</title><body>alice</body></html>",
        )
        results = await engine.check_username("alice")
    assert results[0].status == ABSENT


async def test_login_path_is_unknown_even_with_username():
    site = SiteSpec(
        key="loginwall",
        name="LoginWall",
        platform=Platform.WEB,
        url_template="https://loginwall.example.com/{u}",
        skip_robots=True,
    )
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False), sites=[site], concurrency=1
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://loginwall.example.com/alice").respond(
            200,
            text="<html><title>Login</title>alice</html>",
            headers={"content-type": "text/html"},
        )
        # Respx final URL'yi degistirmedigi icin _classify'i dogrudan login
        # URL'siyle cagir.
        import httpx

        response = httpx.Response(
            200,
            text="<html><title>Login</title>alice</html>",
            request=httpx.Request("GET", "https://loginwall.example.com/login"),
        )
        result = engine._classify(site, "alice", "https://loginwall.example.com/alice", response)
    assert result.status == UNKNOWN
    assert "login" in result.detail


async def test_require_username_false_opts_out():
    site = SiteSpec(
        key="optout",
        name="OptOut",
        platform=Platform.WEB,
        url_template="https://optout.example.com/{u}",
        skip_robots=True,
        require_username=False,
    )
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False), sites=[site], concurrency=1
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://optout.example.com/alice").respond(200, text="ok")
        results = await engine.check_username("alice")
    assert results[0].status == EXISTS


def test_generic_verified_confidence_higher_than_optout():
    from showis_connectors.sitecheck.connector import SiteCheckConnector

    generic = SiteSpec(
        key="g1", name="G1", platform=Platform.WEB, url_template="https://g1/{u}"
    )
    optout = SiteSpec(
        key="g2",
        name="G2",
        platform=Platform.WEB,
        url_template="https://g2/{u}",
        require_username=False,
    )
    assert SiteCheckConnector._confidence(generic) >= 0.65
    assert SiteCheckConnector._confidence(optout) < 0.6


@pytest.mark.asyncio
async def test_ayni_platformdaki_wmn_siteleri_farkli_entity_uretir():
    from showis_connectors.sitecheck.sites import SiteSpec

    first = SiteSpec(
        "wmn_a",
        "Site A",
        Platform.WEB,
        "https://a.example/{u}",
        present_regex=r"user",
        require_username=False,
        id_namespace="wmn_a",
    )
    second = SiteSpec(
        "wmn_b",
        "Site B",
        Platform.WEB,
        "https://b.example/{u}",
        present_regex=r"user",
        require_username=False,
        id_namespace="wmn_b",
    )
    connector = SiteCheckConnector(
        sites=[first, second],
        engine=UsernameCheckEngine(
            http_client=HttpClient(enable_ssrf_guard=False), sites=[first, second]
        ),
    )
    obs = Observation(
        id="o1",
        connector="sitecheck",
        platform=Platform.WEB,
        kind=ObservationKind.PROFILE,
        subject="kerem",
        tier=RiskTier.B,
        data={
            "username": "kerem",
            "checks": [
                {
                    "site": "wmn_a",
                    "name": "Site A",
                    "platform": "web",
                    "status": EXISTS,
                    "url": "https://a.example/kerem",
                    "confidence": 0.75,
                },
                {
                    "site": "wmn_b",
                    "name": "Site B",
                    "platform": "web",
                    "status": EXISTS,
                    "url": "https://b.example/kerem",
                    "confidence": 0.75,
                },
            ],
        },
    )
    entities, _ = connector.normalize(obs)
    assert len({entity.id for entity in entities}) == 2
    assert all(entity.attributes["username"] == "kerem" for entity in entities)
    assert {entity.label for entity in entities} == {"kerem · Site A", "kerem · Site B"}


# ---------------------------------------------------------------------------
# Site sağlığı — 403/429 tekrar denemesini durdur
# ---------------------------------------------------------------------------


async def test_site_sagligi_403_sonrasi_atlar():
    from showis_connectors.sitecheck.health import SiteHealth

    site = SiteSpec(
        key="unhealthy",
        name="Unhealthy",
        platform=Platform.WEB,
        url_template="https://unhealthy.example.com/{u}",
        skip_robots=True,
    )
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False),
        sites=[site],
        concurrency=1,
        health=SiteHealth(ttl_seconds=3600, failure_threshold=1),
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.get("https://unhealthy.example.com/alice").respond(403)
        first = await engine.check_username("alice")
        second = await engine.check_username("alice")
    assert first[0].status == UNKNOWN
    assert second[0].status == SKIPPED
    assert route.call_count == 1


async def test_site_sagligi_basarili_sonuc_sayaci_sifirlar():
    from showis_connectors.sitecheck.health import SiteHealth

    site = SiteSpec(
        key="recovering",
        name="Recovering",
        platform=Platform.WEB,
        url_template="https://recovering.example.com/{u}",
        skip_robots=True,
    )
    health = SiteHealth(ttl_seconds=3600, failure_threshold=2)
    engine = UsernameCheckEngine(
        http_client=HttpClient(enable_ssrf_guard=False),
        sites=[site],
        concurrency=1,
        health=health,
    )
    with respx.mock(assert_all_called=False) as router:
        router.get("https://recovering.example.com/alice").respond(403)
        router.get("https://recovering.example.com/bob").respond(200, text="bob")
        router.get("https://recovering.example.com/carol").respond(403)
        await engine.check_username("alice")
        ok = await engine.check_username("bob")
        carol = await engine.check_username("carol")
    assert ok[0].status == EXISTS
    assert carol[0].status == UNKNOWN

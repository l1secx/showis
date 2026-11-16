"""Herkeese acik publicapi connector testleri — respx mock."""

import pytest
import respx
from showis_connectors.publicapi import (
    BlueskyConnector,
    ChessConnector,
    DevtoConnector,
    DockerHubConnector,
    GitLabConnector,
    GravatarConnector,
    HackerNewsConnector,
    HuggingFaceConnector,
    KeybaseConnector,
    KitsuConnector,
    NpmConnector,
    PypiConnector,
    SteamConnector,
    WordPressConnector,
    gravatar_hash,
)
from showis_contracts import (
    EntityType,
    Platform,
    ScanContext,
    Seed,
    Target,
    TaskSpec,
)
from showis_engine.http import HttpClient


def make_ctx(**kwargs) -> ScanContext:
    return ScanContext(
        case_id="c1",
        scan_id="s1",
        target=Target(first_name="Test", last_name="User", hints={}),
        seeds=kwargs.pop("seeds", []),
        **kwargs,
    )


def make_task(conn_name: str, platform: Platform, **params) -> TaskSpec:
    return TaskSpec(
        id="t1",
        connector=conn_name,
        platform=platform,
        kind="fetch_profile",
        params=params,
    )


# ---------------------------------------------------------------- GitLab


@pytest.mark.asyncio
async def test_gitlab_fetch_and_normalize():
    conn = GitLabConnector(http_client=HttpClient(enable_ssrf_guard=False))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://gitlab.com/api/v4/users").respond(
            200,
            json=[
                {
                    "username": "testuser",
                    "name": "Test User",
                    "bio": "dev",
                    "location": "Istanbul",
                    "avatar_url": "https://gitlab.com/uploads/x.png",
                    "web_url": "https://gitlab.com/testuser",
                    "state": "active",
                }
            ],
        )
        task = make_task("gitlab", Platform.GITLAB, username="testuser")
        obs = await conn.run(task, make_ctx())

    assert obs[0].data["exists"] is True
    assert obs[0].data["name"] == "Test User"
    assert obs[0].evidence, "kanit uretilmeli"

    entities, _ = conn.normalize(obs[0])
    assert len(entities) == 1
    assert entities[0].platform == Platform.GITLAB
    assert entities[0].attributes["location"] == "Istanbul"


@pytest.mark.asyncio
async def test_gitlab_not_found():
    conn = GitLabConnector(http_client=HttpClient(enable_ssrf_guard=False))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://gitlab.com/api/v4/users").respond(200, json=[])
        obs = await conn.run(
            make_task("gitlab", Platform.GITLAB, username="ghost"), make_ctx()
        )
    assert obs[0].data["exists"] is False
    assert conn.normalize(obs[0])[0] == []


# ---------------------------------------------------------------- Keybase


@pytest.mark.asyncio
async def test_keybase_proofs_to_links():
    conn = KeybaseConnector(http_client=HttpClient(enable_ssrf_guard=False))
    payload = {
        "status": 0,
        "them": [
            {
                "basics": {"username": "kbuser", "ctime": 1600000000},
                "proofs_human": [
                    {"proof_type": "github", "nametag": "ghuser"},
                    {"proof_type": "twitter", "nametag": "twuser"},
                ],
            }
        ],
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://keybase.io/_/api/1.0/user/lookup.json").respond(
            200, json=payload
        )
        obs = await conn.run(
            make_task("keybase", Platform.KEYBASE, username="kbuser"), make_ctx()
        )

    data = obs[0].data
    assert data["github_link"] == "https://github.com/ghuser"
    assert data["twitter_link"] == "https://x.com/twuser"
    assert "github" in data["social"]

    entities, _ = conn.normalize(obs[0])
    assert entities[0].attributes["github_link"].endswith("ghuser")


# ----------------------------------------------------------------- Dev.to


@pytest.mark.asyncio
async def test_devto_user():
    conn = DevtoConnector(http_client=HttpClient(enable_ssrf_guard=False))
    payload = {
        "username": "devuser",
        "name": "Dev User",
        "summary": "writer",
        "location": "Berlin",
        "github_username": "devgh",
        "twitter_username": "devtw",
        "website_url": "https://dev.example.com",
        "profile_image": "https://dev.to/i.png",
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://dev.to/api/users/by_username").respond(200, json=payload)
        obs = await conn.run(
            make_task("devto", Platform.DEVTO, username="devuser"), make_ctx()
        )
    assert obs[0].data["github_link"] == "https://github.com/devgh"
    assert obs[0].data["links"]


# --------------------------------------------------------------- DockerHub


@pytest.mark.asyncio
async def test_dockerhub_user():
    conn = DockerHubConnector(http_client=HttpClient(enable_ssrf_guard=False))
    payload = {
        "username": "dockeruser",
        "full_name": "Docker User",
        "date_joined": "2020-01-01T00:00:00Z",
        "profile_url": "https://hub.docker.com/u/dockeruser",
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://hub.docker.com/v2/users/dockeruser/").respond(
            200, json=payload
        )
        obs = await conn.run(
            make_task("dockerhub", Platform.DOCKERHUB, username="dockeruser"),
            make_ctx(),
        )
    assert obs[0].data["name"] == "Docker User"


# -------------------------------------------------------------------- npm


@pytest.mark.asyncio
async def test_npm_maintainer_search():
    conn = NpmConnector(http_client=HttpClient(enable_ssrf_guard=False))
    payload = {
        "total": 2,
        "objects": [
            {"package": {"name": "pkg-a", "version": "1.0.0"}},
            {"package": {"name": "pkg-b", "version": "2.0.0"}},
        ],
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://registry.npmjs.org/-/v1/search").respond(200, json=payload)
        obs = await conn.run(
            make_task("npm", Platform.NPM, username="npmuser"), make_ctx()
        )
    assert obs[0].data["package_count"] == 2
    assert obs[0].data["packages"][0]["name"] == "pkg-a"


# ------------------------------------------------------------------- PyPI


@pytest.mark.asyncio
async def test_pypi_profile_parse():
    conn = PypiConnector(http_client=HttpClient(enable_ssrf_guard=False))
    html = """
    <html><body>
      <a href="/project/pkg-one/">pkg-one</a>
      <a href="/project/pkg-two/">pkg-two</a>
      <a href="/project/pkg-one/">dup</a>
    </body></html>
    """
    with respx.mock(assert_all_called=False) as router:
        router.get("https://pypi.org/user/pypiuser/").respond(200, text=html)
        obs = await conn.run(
            make_task("pypi", Platform.PYPI, username="pypiuser"), make_ctx()
        )
    assert obs[0].data["project_count"] == 2
    assert set(obs[0].data["projects"]) == {"pkg-one", "pkg-two"}


# ------------------------------------------------------------------ Chess


@pytest.mark.asyncio
async def test_chess_player():
    conn = ChessConnector(http_client=HttpClient(enable_ssrf_guard=False))
    payload = {
        "username": "chessuser",
        "name": "Chess User",
        "title": "GM",
        "country": "https://api.chess.com/pub/country/TR",
        "avatar": "https://images.chesscom.com/u.png",
        "joined": 1500000000000,
        "followers": 10,
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.chess.com/pub/player/chessuser").respond(
            200, json=payload
        )
        obs = await conn.run(
            make_task("chess", Platform.CHESS, username="chessuser"), make_ctx()
        )
    assert obs[0].data["title"] == "GM"


# --------------------------------------------------------------- HackerNews


@pytest.mark.asyncio
async def test_hackernews_user_page():
    conn = HackerNewsConnector(http_client=HttpClient(enable_ssrf_guard=False))
    html = """
    <html><body><table>
      <tr><td>user:</td><td>hnuser</td></tr>
      <tr><td>created:</td><td>2020-01-01</td></tr>
      <tr><td>karma:</td><td>12,345</td></tr>
      <tr><td>about:</td><td>I build things</td></tr>
    </table></body></html>
    """
    with respx.mock(assert_all_called=False) as router:
        router.get(
            "https://news.ycombinator.com/user", params={"id": "hnuser"}
        ).respond(200, text=html)
        router.get("https://news.ycombinator.com/user").respond(200, text=html)
        obs = await conn.run(
            make_task("hackernews", Platform.HACKERNEWS, username="hnuser"),
            make_ctx(),
        )
    assert obs[0].data["karma"] == 12345
    assert obs[0].data["about"] == "I build things"


# ---------------------------------------------------------------- Steam


@pytest.mark.asyncio
async def test_steam_profile_page():
    conn = SteamConnector(http_client=HttpClient(enable_ssrf_guard=False))
    html = """
    <html><head>
      <meta property="og:title" content="Steam Community :: SteamUser" />
      <meta property="og:image" content="https://steamcdn/a.jpg" />
    </head><body>Member since March 2015, 12,345 BADGES</body></html>
    """
    with respx.mock(assert_all_called=False) as router:
        router.get("https://steamcommunity.com/id/steamuser").respond(
            200, text=html
        )
        obs = await conn.run(
            make_task("steam", Platform.STEAM, username="steamuser"), make_ctx()
        )
    # Steam og:title once "Steam Community :: <isim>" doner
    assert obs[0].data["name"] == "SteamUser"
    assert obs[0].data["avatar_url"] == "https://steamcdn/a.jpg"


@pytest.mark.asyncio
async def test_steam_not_found_marker():
    conn = SteamConnector(http_client=HttpClient(enable_ssrf_guard=False))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://steamcommunity.com/id/ghost").respond(
            200, text="The specified profile could not be found."
        )
        obs = await conn.run(
            make_task("steam", Platform.STEAM, username="ghost"), make_ctx()
        )
    assert obs[0].data["exists"] is False


# ---------------------------------------------------------------- Gravatar


def test_gravatar_hash_known():
    # md5("test@example.com") bilinen deger
    assert (
        gravatar_hash("test@example.com")
        == "55502f40dc8b7c769880b10874abc9d0"
    )


@pytest.mark.asyncio
async def test_gravatar_email_flow():
    conn = GravatarConnector(http_client=HttpClient(enable_ssrf_guard=False))
    payload = {
        "entry": [
            {
                "displayName": "Grav User",
                "aboutMe": "bio here",
                "currentLocation": "Izmir",
                "profileUrl": "https://gravatar.com/abc",
                "thumbnailUrl": "https://gravatar.com/avatar/abc",
                "accounts": [
                    {
                        "domain": "github.com",
                        "shortname": "github",
                        "username": "ghgrav",
                        "url": "https://github.com/ghgrav",
                    }
                ],
                "emails": [{"value": "test@example.com"}],
            }
        ]
    }
    ctx = make_ctx(
        seeds=[Seed(platform=Platform.UNKNOWN, email="test@example.com")]
    )
    tasks = await conn.plan(ctx)
    assert len(tasks) == 1
    assert tasks[0].params["hash"] == gravatar_hash("test@example.com")

    with respx.mock(assert_all_called=False) as router:
        router.get(
            f"https://en.gravatar.com/{gravatar_hash('test@example.com')}.json"
        ).respond(200, json=payload)
        obs = await conn.run(tasks[0], ctx)

    assert obs[0].data["github_link"] == "https://github.com/ghgrav"
    assert obs[0].data["email"] == "test@example.com"

    entities, _ = conn.normalize(obs[0])
    assert entities[0].platform == Platform.GRAVATAR
    assert entities[0].type == EntityType.ACCOUNT


# ------------------------------------------------------------- plan ortak


@pytest.mark.asyncio
async def test_seed_platform_matching():
    conn = DevtoConnector(http_client=HttpClient(enable_ssrf_guard=False))
    ctx = make_ctx(
        seeds=[
            Seed(platform=Platform.DEVTO, username="seeddev"),
            Seed(platform=Platform.GITHUB, username="other"),
            Seed(platform=Platform.DEVTO, url="https://dev.to/fromurl"),
        ]
    )
    tasks = await conn.plan(ctx)
    idents = {t.params.get("username") for t in tasks if t.priority == 1}
    assert "seeddev" in idents
    assert "fromurl" in idents


# --------------------------------------------- Hata != "hesap yok" davranisi


@pytest.mark.parametrize(
    ("connector_cls", "url"),
    [
        (
            BlueskyConnector,
            "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor=tester.bsky.social",
        ),
        (HuggingFaceConnector, "https://huggingface.co/api/users/tester"),
        (KitsuConnector, "https://kitsu.io/api/edge/users?filter[slug]=tester"),
        (
            WordPressConnector,
            "https://public-api.wordpress.com/rest/v1.1/sites/tester.wordpress.com",
        ),
    ],
)
@pytest.mark.asyncio
async def test_fetch_failure_is_not_reported_as_missing(connector_cls, url):
    """Beklenmeyen HTTP hatasi 'exists: false' gibi gorunmemeli (yanlis negatif)."""
    conn = connector_cls(http_client=HttpClient(enable_ssrf_guard=False))
    with respx.mock(assert_all_called=False) as router:
        router.get(url).respond(403)
        obs = await conn.run(
            make_task("tester", Platform.WEB, username="tester"), make_ctx()
        )
    assert obs[0].error
    assert obs[0].data.get("exists") is not False

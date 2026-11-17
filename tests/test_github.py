import pytest
import respx
from showis_connectors.github import GITHUB_API_BASE, GitHubConnector
from showis_contracts import (
    EntityType,
    ObservationKind,
    Platform,
    ScanContext,
    Seed,
    Target,
    TaskSpec,
)


@pytest.fixture
def github_connector():
    return GitHubConnector(token="test_gh_token")


def _handle_ctx(nickname: str = "kerem") -> ScanContext:
    """Yalnız nick taşıyan hedef (ad/soyad yok)."""
    return ScanContext(
        case_id="c-1",
        scan_id="s-handle-github",
        target=Target(kind="handle", nickname=nickname),
        seeds=[],
    )


@pytest.mark.asyncio
async def test_github_handle_mode_uses_login_search(github_connector: GitHubConnector):
    specs = await github_connector.plan(_handle_ctx("kerem"))
    search_users = [s for s in specs if s.kind == "search_users"]
    assert search_users
    assert search_users[0].params["query"] == "kerem in:login"


@pytest.mark.asyncio
async def test_github_handle_mode_without_nickname_emits_no_search_users(
    github_connector: GitHubConnector,
):
    """Ne ad ne nick varsa `"" in:name` gibi boş sorgu üretilmemeli."""
    ctx = ScanContext(
        case_id="c-1",
        scan_id="s-empty-github",
        target=Target(kind="handle"),
        seeds=[],
    )
    specs = await github_connector.plan(ctx)
    assert all(s.kind != "search_users" for s in specs)


@pytest.mark.asyncio
async def test_github_handle_mode_sanitizes_nick_operators(github_connector: GitHubConnector):
    """Nick de kullanıcı girdisi: tırnak/operatör enjeksiyonu nötralize edilir."""
    specs = await github_connector.plan(_handle_ctx('kerem" in:readme'))
    query = next(s for s in specs if s.kind == "search_users").params["query"]
    assert "in:readme" not in query
    assert '""' not in query
    assert query.endswith("in:login")


@pytest.mark.asyncio
async def test_github_name_mode_still_searches_names(github_connector: GitHubConnector):
    ctx = ScanContext(
        case_id="c-1",
        scan_id="s-name-github",
        target=Target(first_name="Kerem", last_name="Boz"),
        seeds=[],
    )
    spec = next(s for s in await github_connector.plan(ctx) if s.kind == "search_users")
    assert spec.params["query"].startswith('"Kerem Boz" in:name')


@pytest.mark.asyncio
async def test_github_plan(github_connector: GitHubConnector):
    ctx = ScanContext(
        case_id="c-1",
        scan_id="s-1",
        target=Target(first_name="Kerem", last_name="Boz", hints={"city": "Istanbul"}),
        seeds=[
            Seed(platform=Platform.GITHUB, username="keremboz"),
            Seed(platform=Platform.GITHUB, email="kerem@example.com"),
        ],
    )
    tasks = await github_connector.plan(ctx)
    assert len(tasks) >= 3

    kinds = [t.kind for t in tasks]
    assert "get_user" in kinds
    assert "search_commits" in kinds
    assert "search_users" in kinds


@pytest.mark.asyncio
async def test_github_run_get_user_and_normalize(github_connector: GitHubConnector):
    task = TaskSpec(
        id="t-1",
        connector="github",
        platform=Platform.GITHUB,
        kind="get_user",
        params={"username": "octocat"},
    )
    ctx = ScanContext(
        case_id="c-1",
        scan_id="s-1",
        target=Target(first_name="Octo", last_name="Cat"),
    )

    user_payload = {
        "login": "octocat",
        "name": "The Octocat",
        "bio": "GitHub mascot",
        "company": "GitHub",
        "location": "San Francisco",
        "blog": "https://github.blog",
        "twitter_username": "github",
        "avatar_url": "https://avatars.githubusercontent.com/u/583231",
        "public_repos": 8,
        "followers": 1000,
        "html_url": "https://github.com/octocat",
    }

    with respx.mock:
        respx.get(f"{GITHUB_API_BASE}/users/octocat").respond(200, json=user_payload)
        obs_list = await github_connector.run(task, ctx)

        assert len(obs_list) == 1
        obs = obs_list[0]
        assert obs.kind == ObservationKind.PROFILE
        assert obs.subject == "octocat"
        assert len(obs.evidence) == 1

        entities, edges = github_connector.normalize(obs)
        assert len(entities) == 1
        account = entities[0]
        assert account.type == EntityType.ACCOUNT
        assert account.label == "octocat"
        assert account.attributes["name"] == "The Octocat"
        assert account.attributes["bio"] == "GitHub mascot"


@pytest.mark.asyncio
async def test_github_run_rate_limited(github_connector: GitHubConnector):
    task = TaskSpec(
        id="t-2",
        connector="github",
        platform=Platform.GITHUB,
        kind="get_user",
        params={"username": "ratelimited_user"},
    )
    ctx = ScanContext(
        case_id="c-1",
        scan_id="s-1",
        target=Target(first_name="Test", last_name="User"),
    )

    with respx.mock:
        respx.get(f"{GITHUB_API_BASE}/users/ratelimited_user").respond(
            403,
            json={"message": "API rate limit exceeded"},
        )
        obs_list = await github_connector.run(task, ctx)
        assert len(obs_list) == 1
        obs = obs_list[0]
        assert obs.blocked is True or obs.error is not None

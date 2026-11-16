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

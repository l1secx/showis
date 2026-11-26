"""account_intel connector birim testleri (respx; gerçek ağ yok)."""

from __future__ import annotations

import hashlib

import pytest
import respx
from showis_connectors.account_intel import (
    AccountIntelError,
    fetch_devto,
    fetch_github,
    fetch_gitlab,
    fetch_gravatar,
    fetch_hackernews,
    fetch_keybase,
    normalize_link,
    urls_in_text,
)
from showis_engine.http import HttpClient


def _client() -> HttpClient:
    return HttpClient(timeout=5.0, enable_ssrf_guard=False, use_host_limiter=False)


def test_normalize_link_completes_bare_domain_and_rejects_junk():
    assert normalize_link("ali.dev") == "https://ali.dev"
    assert normalize_link("https://x.com/ali") == "https://x.com/ali"
    assert normalize_link("//cdn.example.com/a") == "https://cdn.example.com/a"
    assert normalize_link("not a url") == ""
    assert normalize_link("javascript:alert(1)") == ""


def test_urls_in_text_reads_markdown_and_plain():
    text = "[blog](https://ali.dev) ve https://x.com/ali. tekrar https://ali.dev"
    assert urls_in_text(text) == ["https://ali.dev", "https://x.com/ali"]


@pytest.mark.asyncio
async def test_github_collects_owner_links_readme_and_commit_emails():
    user = {
        "login": "aliyilmaz",
        "html_url": "https://github.com/aliyilmaz",
        "name": "Ali Yılmaz",
        "blog": "ali.dev",
        "twitter_username": "ali_tw",
        "company": "@acme",
        "location": "İstanbul",
        "bio": "Güvenlik https://linktr.ee/aliyilmaz",
        "email": None,
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "created_at": "2015-01-01T00:00:00Z",
    }
    socials = [
        {"provider": "linkedin", "url": "https://www.linkedin.com/in/ali-yilmaz"},
        {"provider": "mastodon", "url": "https://infosec.exchange/@ali"},
    ]
    readme = (
        "![](https://img.shields.io/badge/x) [YouTube](https://youtube.com/@alitube) "
        "https://github.com/torvalds"
    )
    events = [
        {
            "type": "PushEvent",
            "payload": {
                "commits": [
                    {"author": {"email": "ali@ali.dev", "name": "Ali Yılmaz"}},
                    {"author": {"email": "1+ali@users.noreply.github.com", "name": "x"}},
                    {"author": {"email": "other@corp.com", "name": "Bob"}},
                ]
            },
        }
    ]
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.github.com/users/aliyilmaz").respond(200, json=user)
        router.get("https://api.github.com/users/aliyilmaz/social_accounts").respond(
            200, json=socials
        )
        router.get(
            "https://raw.githubusercontent.com/aliyilmaz/aliyilmaz/HEAD/README.md"
        ).respond(200, text=readme)
        router.get("https://api.github.com/users/aliyilmaz/events/public").respond(
            200, json=events
        )
        intel = await fetch_github(_client(), "aliyilmaz")
    assert intel is not None
    urls = {link.url: link.relation for link in intel.links}
    assert urls["https://ali.dev"] == "owner"
    assert urls["https://x.com/ali_tw"] == "owner"
    assert urls["https://github.com/acme"] == "owner"
    assert urls["https://www.linkedin.com/in/ali-yilmaz"] == "owner"
    assert urls["https://linktr.ee/aliyilmaz"] == "bio"
    assert urls["https://youtube.com/@alitube"] == "readme"
    assert not any("shields.io" in url for url in urls)
    # Başkasının commit'i ve noreply adresi alınmaz.
    assert intel.emails == [("ali@ali.dev", "github-commit")]
    assert intel.display_name == "Ali Yılmaz"


@pytest.mark.asyncio
async def test_github_missing_user_returns_none():
    with respx.mock() as router:
        router.get("https://api.github.com/users/yok").respond(404, json={})
        assert await fetch_github(_client(), "yok") is None


@pytest.mark.asyncio
async def test_github_rate_limit_is_readable_error():
    with respx.mock() as router:
        router.get("https://api.github.com/users/ali").respond(
            403, text="API rate limit exceeded"
        )
        with pytest.raises(AccountIntelError, match="hız sınırı"):
            await fetch_github(_client(), "ali")


@pytest.mark.asyncio
async def test_gitlab_social_fields():
    with respx.mock() as router:
        router.get("https://gitlab.com/api/v4/users").respond(200, json=[{"id": 7}])
        router.get("https://gitlab.com/api/v4/users/7").respond(
            200,
            json={
                "username": "ali",
                "web_url": "https://gitlab.com/ali",
                "name": "Ali Yılmaz",
                "website_url": "https://ali.dev",
                "twitter": "ali_tw",
                "linkedin": "ali-yilmaz",
                "public_email": "ali@ali.dev",
            },
        )
        intel = await fetch_gitlab(_client(), "ali")
    assert intel is not None
    urls = {link.url for link in intel.links}
    expected = {"https://ali.dev", "https://x.com/ali_tw", "https://www.linkedin.com/in/ali-yilmaz"}
    assert expected <= urls
    assert intel.emails == [("ali@ali.dev", "gitlab-profile")]


@pytest.mark.asyncio
async def test_keybase_proofs_are_proof_relation():
    payload = {
        "them": [
            {
                "basics": {"username": "ali"},
                "profile": {"full_name": "Ali Yılmaz"},
                "proofs_summary": {
                    "all": [
                        {"proof_type": "github", "nametag": "aliyilmaz", "service_url": ""},
                        {
                            "proof_type": "generic_web_site",
                            "nametag": "ali.dev",
                            "service_url": "https://ali.dev",
                        },
                    ]
                },
            }
        ]
    }
    with respx.mock() as router:
        router.get("https://keybase.io/_/api/1.0/user/lookup.json").respond(200, json=payload)
        intel = await fetch_keybase(_client(), "ali")
    assert intel is not None
    assert {(link.url, link.relation) for link in intel.links} == {
        ("https://github.com/aliyilmaz", "proof"),
        ("https://ali.dev", "proof"),
    }


@pytest.mark.asyncio
async def test_devto_and_hackernews():
    with respx.mock() as router:
        router.get("https://dev.to/api/users/by_username").respond(
            200, json={"username": "ali", "github_username": "aliyilmaz", "name": "Ali"}
        )
        router.get("https://hacker-news.firebaseio.com/v0/user/ali.json").respond(
            200, json={"id": "ali", "about": "site: <a href=\"https://ali.dev\">ali.dev</a>"}
        )
        devto = await fetch_devto(_client(), "ali")
        hn = await fetch_hackernews(_client(), "ali")
    assert devto is not None and devto.links[0].url == "https://github.com/aliyilmaz"
    assert hn is not None and hn.links[0].url == "https://ali.dev"


@pytest.mark.asyncio
async def test_gravatar_v3_verified_accounts():
    email = "ali@ali.dev"
    sha = hashlib.sha256(email.encode()).hexdigest()
    with respx.mock() as router:
        router.get(f"https://api.gravatar.com/v3/profiles/{sha}").respond(
            200,
            json={
                "profile_url": "https://gravatar.com/aliyilmaz",
                "display_name": "Ali Yılmaz",
                "verified_accounts": [
                    {"service_label": "GitHub", "url": "https://github.com/aliyilmaz"}
                ],
            },
        )
        intel = await fetch_gravatar(_client(), email)
    assert intel is not None
    assert intel.handle == "aliyilmaz"
    assert intel.links[0].relation == "proof"


@pytest.mark.asyncio
async def test_gravatar_missing_falls_back_and_returns_none():
    email = "yok@ali.dev"
    sha = hashlib.sha256(email.encode()).hexdigest()
    md5 = hashlib.md5(email.encode()).hexdigest()
    with respx.mock() as router:
        router.get(f"https://api.gravatar.com/v3/profiles/{sha}").respond(404)
        router.get(f"https://en.gravatar.com/{md5}.json").respond(404)
        assert await fetch_gravatar(_client(), email) is None


@pytest.mark.asyncio
async def test_github_head_commit_email_when_push_payload_has_no_commits():
    user = {"login": "ali", "html_url": "https://github.com/ali", "name": "Ali"}
    events = [
        {
            "type": "PushEvent",
            "repo": {"name": "acme/app"},
            "payload": {"head": "a" * 40},
        },
        {
            "type": "PushEvent",
            "repo": {"name": "ali/site"},
            "payload": {"head": "b" * 40},
        },
    ]
    with respx.mock() as router:
        router.get("https://api.github.com/users/ali").respond(200, json=user)
        router.get("https://api.github.com/users/ali/social_accounts").respond(200, json=[])
        router.get("https://raw.githubusercontent.com/ali/ali/HEAD/README.md").respond(404)
        router.get("https://api.github.com/users/ali/events/public").respond(200, json=events)
        # Başkasının commit'i (author.login farklı) alınmaz.
        router.get(f"https://api.github.com/repos/acme/app/commits/{'a' * 40}").respond(
            200,
            json={"author": {"login": "bob"}, "commit": {"author": {"email": "bob@acme.com"}}},
        )
        router.get(f"https://api.github.com/repos/ali/site/commits/{'b' * 40}").respond(
            200,
            json={
                "author": {"login": "ali"},
                "commit": {"author": {"email": "ali@ali.dev", "name": "Ali"}},
            },
        )
        intel = await fetch_github(_client(), "ali")
    assert intel is not None
    assert intel.emails == [("ali@ali.dev", "github-commit")]


@pytest.mark.asyncio
async def test_gitlab_detail_forbidden_falls_back_to_list_row():
    with respx.mock() as router:
        router.get("https://gitlab.com/api/v4/users").respond(
            200, json=[{"id": 1, "username": "ali", "name": "Ali", "web_url": "https://gitlab.com/ali"}]
        )
        router.get("https://gitlab.com/api/v4/users/1").respond(403, json={})
        intel = await fetch_gitlab(_client(), "ali")
    assert intel is not None and intel.display_name == "Ali"
    assert any("403" in note for note in intel.notes)

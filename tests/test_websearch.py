import pytest
import respx
from showis_connectors.websearch import (
    BraveSearchProvider,
    SerpApiSearchProvider,
    extract_platform_from_url,
    normalize_url,
)
from showis_contracts import Platform


def test_url_normalization():
    dirty = "https://www.github.com/octocat/?utm_source=twitter&utm_medium=social&ref=share"
    clean = normalize_url(dirty)
    assert clean == "https://github.com/octocat"

    dirty_trailing = "https://tryhackme.com/p/hacker99/"
    assert normalize_url(dirty_trailing) == "https://tryhackme.com/p/hacker99"


def test_extract_platform_from_url():
    gh_url = "https://github.com/testuser?tab=repositories"
    plat, user = extract_platform_from_url(gh_url)
    assert plat == Platform.GITHUB
    assert user == "testuser"

    thm_url = "https://tryhackme.com/p/sec_enthusiast"
    plat, user = extract_platform_from_url(thm_url)
    assert plat == Platform.TRYHACKME
    assert user == "sec_enthusiast"

    htb_url = "https://app.hackthebox.com/profile/133742"
    plat, user = extract_platform_from_url(htb_url)
    assert plat == Platform.HACKTHEBOX
    assert user == "133742"

    generic_url = "https://example.com/blog/article-1"
    plat, user = extract_platform_from_url(generic_url)
    assert plat == Platform.WEB
    assert user is None


@pytest.mark.asyncio
async def test_brave_search_provider():
    provider = BraveSearchProvider(api_key="test_brave_key")

    mock_resp = {
        "web": {
            "results": [
                {
                    "url": "https://www.github.com/ahmetdev/?utm_campaign=profile",
                    "title": "ahmetdev (Ahmet Dev) · GitHub",
                    "description": "Full-stack developer from Ankara.",
                }
            ]
        }
    }

    with respx.mock:
        respx.get("https://api.search.brave.com/res/v1/web/search").respond(200, json=mock_resp)
        hits = await provider.search("ahmetdev", limit=5)

        assert len(hits) == 1
        hit = hits[0]
        assert hit.provider == "brave"
        assert hit.url == "https://github.com/ahmetdev"
        assert hit.platform == Platform.GITHUB
        assert hit.subject_id == "ahmetdev"


@pytest.mark.asyncio
async def test_serpapi_search_provider():
    provider = SerpApiSearchProvider(api_key="test_serp_key")

    mock_resp = {
        "organic_results": [
            {
                "link": "https://tryhackme.com/p/ahmet_sec",
                "title": "ahmet_sec Profile | TryHackMe",
                "snippet": "Rank 450, 60 rooms completed.",
            }
        ]
    }

    with respx.mock:
        respx.get("https://serpapi.com/search").respond(200, json=mock_resp)
        hits = await provider.search("ahmet_sec", limit=5)

        assert len(hits) == 1
        hit = hits[0]
        assert hit.provider == "serpapi"
        assert hit.url == "https://tryhackme.com/p/ahmet_sec"
        assert hit.platform == Platform.TRYHACKME
        assert hit.subject_id == "ahmet_sec"


_DDG_LITE_HTML = """
<html><body>
<table>
<tr><td><a rel="nofollow"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Foctocat&amp;rut=abc123"
  class='result-link'>The Octocat - GitHub</a></td></tr>
<tr><td class='result-snippet'>GitHub profile of <b>octocat</b> with repos.</td></tr>
<tr><td><span class='link-text'>github.com</span></td></tr>
<tr><td><a rel="nofollow"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftryhackme.com%2Fp%2Fsec_guy&amp;rut=def456"
  class='result-link'>sec_guy Profile | TryHackMe</a></td></tr>
<tr><td class='result-snippet'>Rank 100 on TryHackMe.</td></tr>
</table>
</body></html>
"""


def test_duckduckgo_parse():
    from showis_connectors.websearch import DuckDuckGoSearchProvider

    hits = DuckDuckGoSearchProvider._parse(_DDG_LITE_HTML, limit=10)
    assert len(hits) == 2
    assert hits[0].url == "https://github.com/octocat"
    assert hits[0].title == "The Octocat - GitHub"
    assert "octocat" in hits[0].snippet
    assert hits[0].rank == 1
    assert hits[0].provider == "duckduckgo"
    assert hits[1].url == "https://tryhackme.com/p/sec_guy"
    assert hits[1].platform == Platform.TRYHACKME
    assert hits[1].subject_id == "sec_guy"


@pytest.mark.asyncio
async def test_duckduckgo_search_provider():
    from showis_connectors.websearch import DuckDuckGoSearchProvider

    provider = DuckDuckGoSearchProvider()
    with respx.mock:
        respx.get("https://lite.duckduckgo.com/lite/").respond(200, text=_DDG_LITE_HTML)
        hits = await provider.search("octocat", limit=5)
        assert len(hits) == 2
        assert hits[0].url == "https://github.com/octocat"

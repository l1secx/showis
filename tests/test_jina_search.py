"""Jina Reader üzerinden anahtarsız DDG Lite sağlayıcısı."""

from __future__ import annotations

import httpx
import pytest
import respx
from showis_connectors.websearch.jina import JinaDuckDuckGoSearchProvider

_DDG_HTML = """
<html><body>
<table>
<tr><td><a rel="nofollow" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fl1secc%2F&rut=x"
 class='result-link'>Abdulkerem Demir l1secc - GitHub</a></td></tr>
<tr><td class='result-snippet'>Cyber Sec and software - l1secc</td></tr>
<tr><td><a rel="nofollow" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Flinktr.ee%2Fl1sec&rut=y"
 class='result-link'>l1sec | Linktree</a></td></tr>
<tr><td class='result-snippet'>Kişisel bağlantılar</td></tr>
</table>
</body></html>
"""


@pytest.mark.asyncio
async def test_jina_reader_parses_ddg_lite_html():
    provider = JinaDuckDuckGoSearchProvider()
    with respx.mock(assert_all_called=True) as mock:
        mock.get(url__regex=r"https://r\.jina\.ai/http://lite\.duckduckgo\.com/lite/.*").mock(
            return_value=httpx.Response(200, text=_DDG_HTML)
        )
        hits = await provider.search('"l1secc"', limit=10)
    assert [hit.url for hit in hits] == [
        "https://github.com/l1secc",
        "https://linktr.ee/l1sec",
    ]
    assert all(hit.provider == "jina" for hit in hits)
    assert hits[1].snippet == "Kişisel bağlantılar"


@pytest.mark.asyncio
async def test_jina_reader_error_body_returns_empty():
    provider = JinaDuckDuckGoSearchProvider()
    with respx.mock(assert_all_called=True) as mock:
        mock.get(url__regex=r"https://r\.jina\.ai/.*").mock(
            return_value=httpx.Response(
                200, json={"code": 403, "name": "AbuseAlleviationError"}
            )
        )
        hits = await provider.search("test", limit=5)
    assert hits == []

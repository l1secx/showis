"""Jina Reader üzerinden anahtarsız DuckDuckGo Lite araması.

Bazı ağlarda DuckDuckGo doğrudan erişilemez (bot/ISP engeli). Jina Reader
`r.jina.ai` herkese açık bir okuma proxy'sidir; DDG Lite sayfasını HTML olarak
döndürür. Bu sağlayıcı yalnızca **son çare** zincirindedir; yapılandırılmış
Brave/SerpAPI/CSE anahtarı varsa onlar önce çalışır.

Gizlilik: sorgu metni Jina Reader'a gider. Sorgular kamuya açık OSINT
aramalarıdır; yine de hassas kişisel veri içeren sorgu üretilmemelidir.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from showis_engine.http import HttpClient

from .base import SearchHit, SearchProvider
from .duckduckgo import parse_lite_results

_READER_URL = "https://r.jina.ai/http://lite.duckduckgo.com/lite/"


class JinaDuckDuckGoSearchProvider(SearchProvider):
    name: str = "jina"

    def __init__(self, http_client: HttpClient | None = None):
        self.http_client = http_client or HttpClient(timeout=35.0, enable_ssrf_guard=True)

    async def search(
        self,
        query: str,
        *,
        limit: int = 30,
        site: str | None = None,
    ) -> list[SearchHit]:
        full_query = f"site:{site} {query}" if site else query
        url = f"{_READER_URL}?q={quote_plus(full_query)}"
        resp = await self.http_client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html",
                "X-Return-Format": "html",
            },
            check_robots=False,
            follow_redirects=True,
        )
        resp.raise_for_status()
        body = resp.text
        if not body or body.lstrip().startswith("{"):
            # Jina hata gövdesi (JSON) döndürdüyse sonuç yoktur.
            return []
        hits = parse_lite_results(body, limit=limit)
        for hit in hits:
            hit.provider = self.name
        return hits

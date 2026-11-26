"""DuckDuckGo Lite scraping saglayicisi — anahtarsiz gercek web aramasi.

``lite.duckduckgo.com/lite/`` herkese acik, JS'siz HTML sonuc sayfasi
dondurur. Sonuclar ``class='result-link'`` capa etiketlerinden ve
``uddg=`` yonlendirme parametresinden cikarilir; ozetler
``class='result-snippet'`` hucrelerinden alinir.

Diger saglayicilarin aksine API anahtari gerektirmez; fabrika
yapilandirilmis saglayici yoksa bunu son care olarak kullanir.
"""

from __future__ import annotations

import html
import re
from urllib.parse import unquote

from showis_engine.http import HttpClient

from .base import SearchHit, SearchProvider

_LITE_URL = "https://lite.duckduckgo.com/lite/"

#: <a ... class='result-link' ...>baslik</a> — href ile class'in
#: siralamasindan bagimsiz eslesmek icin lookahead kullanilir.
_LINK_RE = re.compile(
    r"<a\b(?=[^>]*class=[\"']result-link[\"'])[^>]*>(.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r"<td[^>]*class=[\"']result-snippet[\"'][^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_UDDG_RE = re.compile(r"[?&]uddg=([^&]+)")
#: Reklam/yonlendirme baglantilari sonuc olarak kullanilamaz (gercek URL yok).
_TRACKER_RE = re.compile(
    r"(?:duckduckgo\.com/(?:l/|y\.js)|google\.[a-z.]+/goto|/aclk\?|bing\.com/aclick)",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _extract_url(href: str) -> str:
    """DDG /l/?uddg=... yonlendirme baglantisindan gercek URL'yi cozer."""
    m = _UDDG_RE.search(href)
    url = unquote(html.unescape(m.group(1)) if m else href)
    if url.startswith("//"):
        url = "https:" + url
    return url


def _clean(text: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", text)).strip()


class DuckDuckGoSearchProvider(SearchProvider):
    name: str = "duckduckgo"

    def __init__(self, http_client: HttpClient | None = None):
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)

    async def search(
        self,
        query: str,
        *,
        limit: int = 30,
        site: str | None = None,
    ) -> list[SearchHit]:
        full_query = f"site:{site} {query}" if site else query
        resp = await self.http_client.get(
            _LITE_URL,
            params={"q": full_query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html",
            },
            check_robots=False,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return self._parse(resp.text, limit=limit)

    # ------------------------------------------------------------------
    # HTML ayristirma (ayri tutulur — test edilebilir)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(page: str, *, limit: int) -> list[SearchHit]:
        """Geriye uyumlu sarmalayıcı (testler bu adı kullanır)."""
        return parse_lite_results(page, limit=limit)


def parse_lite_results(page: str, *, limit: int) -> list[SearchHit]:
    """DDG Lite HTML'inden sonuç linkleri ve snippet'lerini çıkarır."""
    events: list[tuple[int, str, re.Match[str]]] = [
        (m.start(), "link", m) for m in _LINK_RE.finditer(page)
    ]
    events += [(m.start(), "snippet", m) for m in _SNIPPET_RE.finditer(page)]
    events.sort(key=lambda e: e[0])

    hits: list[SearchHit] = []
    for _pos, kind, m in events:
        if kind == "link":
            anchor = m.group(0)
            open_tag = anchor[: anchor.find(">") + 1]
            href_m = _HREF_RE.search(open_tag)
            href = html.unescape(href_m.group(1)) if href_m else ""
            url = _extract_url(href)
            if not url or _TRACKER_RE.search(url):
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=_clean(m.group(1)),
                    snippet="",
                    rank=len(hits) + 1,
                    provider=DuckDuckGoSearchProvider.name,
                    raw={"href": href},
                )
            )
            if len(hits) >= limit:
                break
        elif hits:
            # snippet, kendinden once gelen sonuca aittir
            snippet = _clean(m.group(1))
            if snippet:
                hits[-1].snippet = snippet

    return hits

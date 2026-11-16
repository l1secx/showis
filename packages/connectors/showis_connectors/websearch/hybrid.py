"""Çoklu sağlayıcı destekli hibrit arama motoru (Tier C).

Birden çok arama sağlayıcısını (Brave, SerpAPI, CSE, DuckDuckGo) tek bir
arayüzde birleştirir. Birincil sağlayıcı hata verdiğinde veya yetersiz
sonuç döndüğünde zincirdeki bir sonrakine düşer (fallback chain).
Sonuçları kanonik URL'e göre tekilleştirir (deduplicate) ve sıralar.
"""

from __future__ import annotations

import logging
from typing import Sequence

from .base import SearchHit, SearchProvider

logger = logging.getLogger("showis.websearch.hybrid")


class HybridSearchProvider:
    name: str = "hybrid"

    def __init__(
        self,
        providers: Sequence[SearchProvider],
        *,
        min_results_before_fallback: int = 2,
    ):
        self.providers = list(providers)
        self.min_results_before_fallback = min_results_before_fallback

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        site: str | None = None,
    ) -> list[SearchHit]:
        if not self.providers:
            return []

        all_hits: list[SearchHit] = []
        seen_urls: set[str] = set()

        for provider in self.providers:
            try:
                hits = await provider.search(query, limit=limit, site=site)
                for h in hits:
                    clean_url = h.url.split("#")[0].rstrip("/").lower()
                    if clean_url not in seen_urls:
                        seen_urls.add(clean_url)
                        all_hits.append(h)

                # Yeterli sonuç elde edildiyse diğer sağlayıcılara istek atmaya gerek yok
                if len(all_hits) >= self.min_results_before_fallback:
                    break
            except Exception as exc:
                logger.warning(
                    "Arama sağlayıcısı (%s) başarısız oldu, sonrakine geçiliyor: %s",
                    provider.name,
                    exc,
                )

        # Sıralama ve sınırlandırma
        for idx, hit in enumerate(all_hits):
            hit.rank = idx + 1
        return all_hits[:limit]

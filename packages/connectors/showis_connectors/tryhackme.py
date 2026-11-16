"""TryHackMe connector (Tier B).

Profil URL deseni: https://tryhackme.com/p/<username>
Site Vercel Security Checkpoint arkasindadir; duz HTTP 429 doner.
Bu nedenle toplama BrowserClient (Playwright) uzerinden yapilir.
Engelleme durumunda bypass denenmez — blocked Observation + SERP snippet fallback.
"""

from __future__ import annotations

import re
from typing import Any

from showis_contracts import Platform, ScanContext, TaskSpec
from showis_engine.browser import BrowserClient
from showis_engine.http import EvidenceStore, HttpClient

from .rendered import RenderedProfileConnector
from .websearch.base import SearchProvider

THM_PROFILE_RE = re.compile(r"tryhackme\.com/p/([A-Za-z0-9_.-]+)", re.IGNORECASE)

# TryHackMe profil metninden alan cikarimi icin regex'ler
_RANK_RE = re.compile(r"rank[^\d]{0,15}(?:#\s*)?([\d,]{1,10})", re.IGNORECASE)
_STREAK_RE = re.compile(r"streak[^\d]{0,15}(\d{1,4})", re.IGNORECASE)
_ROOMS_DONE_RE = re.compile(r"rooms?\s*(?:completed|done)[^\d]{0,15}(\d{1,5})", re.IGNORECASE)
_ROOMS_NUM_RE = re.compile(r"(\d{1,5})\s*rooms?\b", re.IGNORECASE)


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    if not m:
        return None
    for group in m.groups():
        if group:
            try:
                return int(group.replace(",", ""))
            except ValueError:
                continue
    return None


class TryHackMeConnector(RenderedProfileConnector):
    name = "tryhackme"
    platform = Platform.TRYHACKME
    search_site = "tryhackme.com"
    profile_url_template = "https://tryhackme.com/p/{id}"

    def __init__(
        self,
        browser_client: BrowserClient | None = None,
        search_provider: SearchProvider | None = None,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
        max_variant_tasks: int = 8,
        max_profile_fetches_per_search: int = 5,
    ):
        super().__init__(
            browser_client=browser_client,
            search_provider=search_provider,
            http_client=http_client,
            evidence_store=evidence_store,
            max_variant_tasks=max_variant_tasks,
            max_profile_fetches_per_search=max_profile_fetches_per_search,
        )

    def identifier_from_url(self, url: str) -> str | None:
        m = THM_PROFILE_RE.search(url or "")
        return m.group(1) if m else None

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seen: set[str] = set()

        # 1. Seed'ler (platform=tryhackme veya URL'den taninan)
        for seed in ctx.seeds:
            ident: str | None = None
            if seed.platform == Platform.TRYHACKME:
                ident = self._seed_identifier(seed)
            elif seed.url:
                ident = self.identifier_from_url(seed.url)
            if ident and ident not in seen:
                seen.add(ident)
                tasks.append(
                    self._make_task(
                        "fetch_profile",
                        {"username": ident, "url": self.profile_url(ident)},
                        priority=1,
                    )
                )

        # 2. Isimden turetilen varyantlar (ilk N tanesi)
        tasks.extend(self._variant_tasks(ctx, seen))

        # 3. SERP kesif gorevi (arama saglayici varsa)
        search_task = self._search_task(ctx)
        if search_task:
            tasks.append(search_task)

        return tasks

    def parse_profile(self, html: str, url: str, identifier: str) -> dict[str, Any]:
        data = super().parse_profile(html, url, identifier)
        text = data.get("text_excerpt", "")

        # THM'e ozgu alanlar
        data["username"] = identifier
        data["rank"] = _first_int(_RANK_RE, text)
        data["streak"] = _first_int(_STREAK_RE, text)
        data["rooms_completed"] = _first_int(_ROOMS_DONE_RE, text) or _first_int(
            _ROOMS_NUM_RE, text
        )

        # Sosyal linkler: github_link oncelikli, digerleri social'dan
        social = data.get("social") or {}
        if not data.get("github_link") and social.get("github"):
            data["github_link"] = f"https://github.com/{social['github']}"
        data["twitter_link"] = (
            f"https://x.com/{social['twitter']}" if social.get("twitter") else None
        )
        data["telegram_link"] = (
            f"https://t.me/{social['telegram']}" if social.get("telegram") else None
        )
        data["linkedin"] = (
            f"https://linkedin.com/in/{social['linkedin']}" if social.get("linkedin") else None
        )
        return data

    def _entity_attributes(self, data: dict[str, Any]) -> dict[str, Any]:
        attrs = super()._entity_attributes(data)
        # github_link vb. alanlari attributes'a tasi
        for key in ("github_link", "twitter_link", "telegram_link", "tryhackme_link"):
            if data.get(key):
                attrs[key] = data[key]
        # display_name -> name (resolver name_similarity icin)
        if data.get("display_name"):
            attrs["name"] = data["display_name"]
        return attrs

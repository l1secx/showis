"""HackTheBox connector (Tier B).

Profil URL'leri: https://app.hackthebox.com/profile/<id> ve
https://www.hackthebox.com/profile/<id> — her ikisi de SPA shell dondurur,
veri ancak tarayicida JS calisinca gelir (Bolum 3.2).

KESIN KURAL: HTB labs API host'u robots.txt'de `Disallow: /` — bu host'a
HICBIR kosulda istek atilmaz (H4); liste packages/engine/showis_engine/policy.py
icindeki FORBIDDEN_HOSTS icinde tutulur. Kod tabani yalnizca app./www. profil
sayfalarini ve arama motoru sonuclarini kullanir.
"""

from __future__ import annotations

import re
from typing import Any

from showis_contracts import Observation, ObservationKind, Platform, ScanContext, TaskSpec
from showis_engine.browser import BrowserClient
from showis_engine.http import EvidenceStore, HttpClient

from .rendered import RenderedProfileConnector
from .websearch.base import SearchProvider

HTB_PROFILE_RE = re.compile(
    r"(?:app\.|www\.)?hackthebox\.com/profile/([A-Za-z0-9_-]+)", re.IGNORECASE
)

# HTB profil metninden alan cikarimi
_RANK_RE = re.compile(r"rank[^\w]{0,15}(captures?|root|pro|user|scriptkiddie)", re.IGNORECASE)
# T-347: SPA HTML'inde gomulu JSON'da kullanici adi / katilim tarihi / puan
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_JSON_USERNAME_RE = re.compile(r'"username"\s*:\s*"([A-Za-z0-9_.-]{1,64})"')
_JSON_JOINED_RE = re.compile(r'"(?:joinedAt|joinDate|createdAt)"\s*:\s*"([0-9T:\-Z.]+)"')
_POINTS_RE = re.compile(r"(?:points|puan)[^\d]{0,12}(\d{1,7})", re.IGNORECASE)
_JOINED_TEXT_RE = re.compile(
    r"(?:joined|member since|katılım)[^\w]{0,12}([A-Z][a-z]+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_MACHINES_RE = re.compile(
    r"machines?\s*(?:completed|owned)?[^\d]{0,15}(\d{1,4})", re.IGNORECASE
)
_COUNTRY_RE = re.compile(r"[Cc]ountry[^\w]{0,10}([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})")
_SPA_SHELL_THRESHOLD = 80  # karakterin altindaki metin = JS'siz shell


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    m = pattern.search(text)
    if not m:
        return None
    for group in m.groups():
        if group and group.isdigit():
            return int(group)
    return None


class HackTheBoxConnector(RenderedProfileConnector):
    name = "hackthebox"
    platform = Platform.HACKTHEBOX
    search_site = "hackthebox.com/profile"
    profile_url_template = "https://app.hackthebox.com/profile/{id}"

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
        m = HTB_PROFILE_RE.search(url or "")
        return m.group(1) if m else None

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seen: set[str] = set()

        for seed in ctx.seeds:
            if seed.platform != Platform.HACKTHEBOX and not (
                seed.url and self.identifier_from_url(seed.url)
            ):
                continue
            if seed.url:
                profile_id = self.identifier_from_url(seed.url)
                if profile_id and profile_id not in seen:
                    seen.add(profile_id)
                    tasks.append(
                        self._make_task(
                            "fetch_profile",
                            {
                                "profile_id": profile_id,
                                "url": f"https://app.hackthebox.com/profile/{profile_id}",
                            },
                            priority=1,
                        )
                    )
            elif seed.username:
                # Kullanici adi -> profil id cozumu SERP uzerinden (oncelikli yontem)
                tasks.append(
                    self._make_task(
                        "resolve",
                        {"username": seed.username},
                        priority=1,
                    )
                )
                seen.add(seed.username)

        # Varyantlar: yalnizca arama saglayici varsa resolve gorevi anlamli
        if self.search_provider:
            from showis_engine.variants import generate_variants

            variant_set = generate_variants(
                ctx.target.first_name, ctx.target.last_name, ctx.target.hints or {}
            )
            count = 0
            for v in variant_set.variants:
                if v.username in seen:
                    continue
                seen.add(v.username)
                tasks.append(
                    self._make_task("resolve", {"username": v.username}, priority=4)
                )
                count += 1
                if count >= self.max_variant_tasks:
                    break

        search_task = self._search_task(ctx)
        if search_task:
            tasks.append(search_task)

        return tasks

    # ------------------------------------------------------------------
    # Run — resolve (username -> profile id)
    # ------------------------------------------------------------------

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        if task.kind == "resolve":
            return await self._run_resolve(task)
        return await super().run(task, ctx)

    async def _run_resolve(self, task: TaskSpec) -> list[Observation]:
        username = str(task.params.get("username", ""))
        if not self.search_provider:
            return [
                self._base_observation(
                    ObservationKind.SEARCH_RESULT,
                    username,
                    error="HTB username->profile_id cozumu icin arama saglayicisi gerekli (BYOK).",
                )
            ]

        query = f'"{username}" site:hackthebox.com/profile'
        try:
            hits = await self.search_provider.search(query, limit=10)
        except Exception as exc:
            return [
                self._base_observation(
                    ObservationKind.SEARCH_RESULT, username, error=str(exc)
                )
            ]

        observations = [
            self._base_observation(
                ObservationKind.SEARCH_RESULT,
                username,
                data={"query": query, "hits": [h.model_dump() for h in hits]},
            )
        ]

        # Ilk eslesen HTB profil id'sini cozumle
        profile_id: str | None = None
        for hit in hits:
            profile_id = self.identifier_from_url(hit.url)
            if profile_id:
                break

        if profile_id:
            observations.extend(
                await self._render_profile(
                    profile_id, f"https://app.hackthebox.com/profile/{profile_id}"
                )
            )
        else:
            observations.append(
                self._base_observation(
                    ObservationKind.SEARCH_RESULT,
                    username,
                    data={"query": query, "resolved_profile_id": None},
                )
            )
        return observations

    # ------------------------------------------------------------------
    # Parse / normalize
    # ------------------------------------------------------------------

    def parse_profile(self, html: str, url: str, identifier: str) -> dict[str, Any]:
        from showis_engine.htmlutil import strip_html as _strip

        text = _strip(html)

        # SPA shell tespiti: JS'siz icerik cok kisaysa veri yok
        if len(text) < _SPA_SHELL_THRESHOLD:
            return {
                "identifier": identifier,
                "profile_id": identifier,
                "profile_url": url,
                "spa_shell": True,
                "text_excerpt": text[:1000],
            }

        data = super().parse_profile(html, url, identifier)
        data["profile_id"] = identifier
        # T-347: platform kimligi (uuid) ile kullanici adi AYRILIR. Profilde
        # okunabilir bir kullanici adi varsa entity kimligi ondan uretilir
        # (rendered.normalize -> f"{platform}:{username}"); uuid yalnizca
        # `platform_user_id` alaninda saklanir.
        username = self._extract_username(html, data, identifier)
        if username:
            data["username"] = username
        else:
            data.pop("username", None)
        data["platform_user_id"] = identifier

        joined_m = _JSON_JOINED_RE.search(html) if html else None
        if joined_m:
            data["joined_at"] = joined_m.group(1)
        else:
            joined_text = _JOINED_TEXT_RE.search(text)
            data["joined_at"] = joined_text.group(1) if joined_text else None
        data["points"] = _first_int(_POINTS_RE, text)

        rank_m = _RANK_RE.search(text)
        data["rank"] = rank_m.group(1).strip().title() if rank_m else None
        data["machines_completed"] = _first_int(_MACHINES_RE, text)

        country_m = _COUNTRY_RE.search(text)
        data["country"] = country_m.group(1).strip() if country_m else None
        if data.get("country"):
            data["location"] = data["country"]

        social = data.get("social") or {}
        data["github_link"] = (
            f"https://github.com/{social['github']}" if social.get("github") else None
        ) or data.get("github_link")
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

    @staticmethod
    def _extract_username(html: str, data: dict[str, Any], identifier: str) -> str | None:
        """Okunabilir kullanici adini bulur; uuid asla kullanici adi sayilmaz.

        Kaynak sirasi: gomulu JSON `"username"` -> `display_name` (uuid degilse)
        -> identifier (uuid degilse). Bulunamazsa None (kimlik uuid kalir).
        """
        if html:
            match = _JSON_USERNAME_RE.search(html)
            if match:
                candidate = match.group(1).strip()
                if candidate and not _UUID_RE.match(candidate):
                    return candidate
        candidates = (
            str(data.get("display_name") or "").strip(),
            str(identifier or "").strip(),
        )
        for candidate in candidates:
            if candidate and not _UUID_RE.match(candidate) and " " not in candidate:
                return candidate
        return None

    def _entity_attributes(self, data: dict[str, Any]) -> dict[str, Any]:
        attrs = super()._entity_attributes(data)
        for key in (
            "profile_id",
            "platform_user_id",
            "username",
            "country",
            "machines_completed",
            "rank",
            "points",
            "joined_at",
            "created_at",
            "bio",
        ):
            if data.get(key) is not None:
                attrs[key] = data[key]
        for key in ("github_link", "twitter_link", "telegram_link", "linkedin"):
            if data.get(key):
                attrs[key] = data[key]
        if data.get("display_name"):
            attrs["name"] = data["display_name"]
        if attrs.get("joined_at") and not attrs.get("created_at"):
            attrs["created_at"] = attrs["joined_at"]
        return attrs

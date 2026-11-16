"""Tarayıcı servisi üzerinden profil çeken connector'lar için ortak taban.

Tier B kaynaklar (TryHackMe, HackTheBox): Playwright render sonucu HTML
işlenir; engelleme (checkpoint/429) durumunda asla bypass denenmez —
`Observation(blocked=True)` üretilir ve varsa SERP snippet fallback'e düşülür.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Any

from showis_contracts import (
    Edge,
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    Seed,
    TaskSpec,
    make_entity_id,
)
from showis_engine.browser import RENDER_USER_AGENT, BrowserClient
from showis_engine.htmlutil import og_image, og_title, strip_html
from showis_engine.http import EvidenceStore, HttpClient
from showis_engine.links import (
    build_cross_link_edges,
    extract_profile_links,
    extract_social_links,
    fetch_avatar_sha256,
)

from .websearch.base import SearchProvider

logger = logging.getLogger("showis.rendered_connector")

#: UUID biçimli kimlikler etiket olarak kullanılmaz (okunamaz)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class RenderedProfileConnector:
    """BrowserClient + opsiyonel SearchProvider ile çalışan profil connector tabanı."""

    name: str = "rendered"
    platform: Platform = Platform.UNKNOWN
    tier: RiskTier = RiskTier.B
    supported_kinds: set[str] = {"fetch_profile", "search"}

    #: SERP site: operatörü için kullanılır (örn. "tryhackme.com")
    search_site: str = ""
    #: canonical profil URL şablonu — {id} yerine konur
    profile_url_template: str = ""

    def __init__(
        self,
        browser_client: BrowserClient | None = None,
        search_provider: SearchProvider | None = None,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
        max_variant_tasks: int = 8,
        max_profile_fetches_per_search: int = 5,
    ):
        self.browser_client = browser_client
        self.search_provider = search_provider
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)
        self.evidence_store = evidence_store or EvidenceStore()
        self.max_variant_tasks = max_variant_tasks
        self.max_profile_fetches_per_search = max_profile_fetches_per_search

    # ------------------------------------------------------------------
    # Plan helpers
    # ------------------------------------------------------------------

    def profile_url(self, identifier: str) -> str:
        return self.profile_url_template.format(id=identifier)

    def _make_task(self, kind: str, params: dict[str, Any], priority: int = 5) -> TaskSpec:
        return TaskSpec(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind=kind,
            params=params,
            priority=priority,
        )

    def _seed_identifier(self, seed: Seed) -> str | None:
        """Seed'den platform tanımlayıcısını (username veya profil id) çıkarır."""
        if seed.username:
            return str(seed.username)
        if seed.url:
            return self.identifier_from_url(seed.url)
        return None

    def identifier_from_url(self, url: str) -> str | None:
        """Profil URL'sinden tanımlayıcıyı çıkarır (alt sınıfça ezilebilir)."""
        return None

    def _variant_tasks(self, ctx: ScanContext, existing: set[str]) -> list[TaskSpec]:
        from showis_engine.variants import generate_variants

        target = ctx.target
        variant_set = generate_variants(
            target.first_name, target.last_name, target.hints or {}
        )
        tasks: list[TaskSpec] = []
        for v in variant_set.variants:
            if v.username in existing:
                continue
            existing.add(v.username)
            tasks.append(
                self._make_task(
                    "fetch_profile",
                    {"username": v.username},
                    priority=4,
                )
            )
            if len(tasks) >= self.max_variant_tasks:
                break
        return tasks

    def _search_task(self, ctx: ScanContext) -> TaskSpec | None:
        if not self.search_provider:
            return None
        fullname = f"{ctx.target.first_name} {ctx.target.last_name}".strip()
        return self._make_task(
            "search",
            {"query": f'"{fullname}" site:{self.search_site}', "limit": 10},
            priority=2,
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _base_observation(
        self, kind: ObservationKind, subject: str, **kwargs: Any
    ) -> Observation:
        return Observation(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind=kind,
            subject=subject,
            tier=self.tier,
            **kwargs,
        )

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        try:
            if task.kind == "fetch_profile":
                return await self._run_fetch_profile(task)
            if task.kind == "search":
                return await self._run_search(task)
            return [
                self._base_observation(
                    ObservationKind.SEARCH_RESULT,
                    str(task.params),
                    error=f"Bilinmeyen görev türü: {task.kind}",
                )
            ]
        except Exception as exc:
            logger.exception("Connector run hatası (%s): %s", self.name, exc)
            return [
                self._base_observation(
                    ObservationKind.SEARCH_RESULT,
                    str(task.params.get("username") or task.params.get("query", "")),
                    error=str(exc),
                )
            ]

    async def _run_fetch_profile(self, task: TaskSpec) -> list[Observation]:
        ident = task.params.get("username") or task.params.get("profile_id") or ""
        url = task.params.get("url") or self.profile_url(ident)
        return await self._render_profile(ident, url)

    async def _run_search(self, task: TaskSpec) -> list[Observation]:
        query = task.params.get("query", "")
        limit = int(task.params.get("limit", 10))

        if not self.search_provider:
            return [
                self._base_observation(
                    ObservationKind.SEARCH_RESULT,
                    query,
                    error="Arama sağlayıcısı yapılandırılmamış (BYOK gerekli).",
                )
            ]

        hits = await self.search_provider.search(query, limit=limit)
        observations = [
            self._base_observation(
                ObservationKind.SEARCH_RESULT,
                query,
                url=None,
                data={
                    "query": query,
                    "hits": [h.model_dump() for h in hits],
                },
            )
        ]

        # Platform eşleşen ilk N hit için profili render et
        fetched = 0
        for hit in hits:
            if fetched >= self.max_profile_fetches_per_search:
                break
            ident = self.identifier_from_url(hit.url)
            if not ident:
                continue
            observations.extend(await self._render_profile(ident, self.profile_url(ident)))
            fetched += 1
        return observations

    async def _render_profile(self, identifier: str, url: str) -> list[Observation]:
        if self.browser_client is None:
            return [
                self._base_observation(
                    ObservationKind.PROFILE,
                    identifier,
                    url=url,
                    error="Browser servisi istemcisi yapılandırılmamış (BROWSER_SERVICE_URL).",
                )
            ]

        result = await self.browser_client.render(url)

        if result.blocked or result.status_code >= 400 or not result.html:
            obs = self._base_observation(
                ObservationKind.BLOCKED,
                identifier,
                url=url,
                blocked=True,
                data={
                    "reason": "bot koruması / engelleme / boş yanıt",
                    "http_status": result.status_code,
                },
            )
            # SERP fallback yalnizca gercek engelleme/5xx'te anlamli;
            # 404 (profil yok) icin provider cagrisi bosa gider.
            status = result.status_code or 0
            needs_fallback = result.blocked or status in (401, 403, 429) or status >= 500
            fallback = await self._serp_fallback(identifier) if needs_fallback else []
            return [obs, *fallback]

        html_bytes = result.html.encode("utf-8", errors="replace")
        evidence = self.evidence_store.store_bytes(
            html_bytes,
            source_url=url,
            kind="html",
            http_status=result.status_code,
            user_agent=RENDER_USER_AGENT,
        )

        data = self.parse_profile(result.html, url, identifier)
        avatar_url = data.get("avatar_url")
        data["avatar_sha256"] = await fetch_avatar_sha256(avatar_url, self.http_client)

        return [
            self._base_observation(
                ObservationKind.PROFILE,
                identifier,
                url=url,
                data=data,
                evidence=[evidence],
            )
        ]

    async def _serp_fallback(self, subject: str) -> list[Observation]:
        """Engellenme durumunda SERP snippet gözlemi üretir (Tier C — siteye istek atılmaz)."""
        if not self.search_provider:
            return []
        try:
            hits = await self.search_provider.search(
                f'"{subject}" site:{self.search_site}', limit=5
            )
        except Exception as exc:
            logger.warning("SERP fallback başarısız (%s): %s", subject, exc)
            return []
        return [
            self._base_observation(
                ObservationKind.SNIPPET,
                subject,
                url=hit.url,
                data={
                    "title": hit.title,
                    "snippet": hit.snippet,
                    "rank": hit.rank,
                    "provider": hit.provider,
                },
            )
            for hit in hits
        ]

    # ------------------------------------------------------------------
    # Parsing / normalization — alt sınıfça uygulanır
    # ------------------------------------------------------------------

    def parse_profile(self, html: str, url: str, identifier: str) -> dict[str, Any]:
        """Render edilmiş HTML'den profil alanlarını çıkarır."""
        text = strip_html(html)
        links = extract_profile_links(html)
        social = extract_social_links(text)

        profile_links = [lnk.url for lnk in links]
        github_link = next((lnk.url for lnk in links if lnk.platform == Platform.GITHUB), None)
        thm_link = next((lnk.url for lnk in links if lnk.platform == Platform.TRYHACKME), None)
        htb_link = next((lnk.url for lnk in links if lnk.platform == Platform.HACKTHEBOX), None)

        return {
            "identifier": identifier,
            "profile_url": url,
            "title": og_title(html),
            "display_name": self._display_name(html, identifier),
            "avatar_url": og_image(html),
            "links": profile_links,
            "social": social,
            "github_link": github_link,
            "tryhackme_link": thm_link,
            "hackthebox_link": htb_link,
            "text_excerpt": text[:4000],
        }

    def _display_name(self, html: str, identifier: str) -> str | None:
        title = og_title(html)
        if title:
            # "Username | TryHackMe" gibi sonekleri temizle
            for sep in ("|", "-", "–", "—", "·"):
                if sep in title:
                    title = title.split(sep)[0].strip()
                    break
            if title:
                return str(title)
        return None

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        entities: list[Entity] = []
        edges: list[Edge] = []

        if obs.error or obs.blocked or not obs.data:
            return entities, edges

        ev_ids = [ev.id for ev in obs.evidence]

        if obs.kind == ObservationKind.PROFILE:
            data = obs.data
            username = data.get("username")
            display_name = data.get("display_name")
            # Kimlik: kullanıcı adı → identifier → subject (T-347 semantiği)
            identifier = username or data.get("identifier") or obs.subject
            if not identifier:
                return entities, edges

            # Etiket: kullanıcı adı → görünen ad. UUID asla etiket olmaz
            # (okunamaz; platform kimliği `attributes.platform_user_id`de).
            # Okunabilir ad yoksa tekil, deterministik kısa tutamak üretilir —
            # etiket aynı zamanda kullanıcı adı gruplamasında anahtar olduğu
            # için (scanner `_entity_username`) **benzersiz** olmak zorunda.
            candidate = username or display_name
            if candidate and not _UUID_RE.match(str(candidate)):
                label = str(candidate)
            else:
                digest = hashlib.sha1(str(identifier).encode("utf-8")).hexdigest()[:8]
                label = f"{self.platform.value}:{digest}"

            entity = Entity(
                id=make_entity_id(self.platform.value, str(identifier)),
                type=EntityType.ACCOUNT,
                platform=self.platform,
                label=label,
                url=data.get("profile_url") or obs.url,
                attributes=self._entity_attributes(data),
                evidence_ids=ev_ids,
            )
            entities.append(entity)
            edges.extend(build_cross_link_edges(entity, entities))

        elif obs.kind in (ObservationKind.SEARCH_RESULT, ObservationKind.SNIPPET):
            hits = obs.data.get("hits") or [obs.data]
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                ident = self.identifier_from_url(str(hit.get("url", "")))
                if not ident:
                    continue
                entity = Entity(
                    id=make_entity_id(self.platform.value, str(ident)),
                    type=EntityType.ACCOUNT,
                    platform=self.platform,
                    label=str(ident),
                    url=str(hit.get("url") or self.profile_url(ident)),
                    attributes={"source": "search_index"},
                    evidence_ids=ev_ids,
                )
                entities.append(entity)

        return entities, edges

    def _entity_attributes(self, data: dict[str, Any]) -> dict[str, Any]:
        attrs = {
            "name": data.get("display_name"),
            "avatar_url": data.get("avatar_url"),
            "avatar_sha256": data.get("avatar_sha256"),
            "links": data.get("links") or [],
        }
        for key in ("rank", "streak", "rooms_completed", "country", "machines_completed", "badges"):
            if data.get(key) is not None:
                attrs[key] = data[key]
        for key in ("github_link", "twitter_link", "telegram_link", "linkedin"):
            if data.get(key):
                attrs[key] = data[key]
        social = data.get("social") or {}
        for name, ident in social.items():
            attrs.setdefault(name, ident)
        return {k: v for k, v in attrs.items() if v is not None}

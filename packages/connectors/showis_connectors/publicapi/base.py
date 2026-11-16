"""Herkese acik API / sayfa tabanli profil connector'lari.

Giris gerektirmeyen, resmî veya kamuya acik endpoint'lerden gercek profil
verisi ceker (Tier A public API veya Tier B acik sayfa). Her connector
``fetch(identifier)`` implemente eder; taban sinif plan/run/normalize
akisini ve Evidence uretimini yonetir.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
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
)
from showis_engine.http import EvidenceStore, HttpClient

logger = logging.getLogger("showis.publicapi")


@dataclass
class ProfileFetch:
    """fetch() donusu: normalize edilmis profil verisi + ham kanit."""

    url: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: bytes | None = None
    http_status: int | None = None
    evidence_kind: str = "json"


class PublicProfileConnector:
    """Tek profilli, anahtarsiz connector tabani."""

    name: str = "publicapi"
    platform: Platform = Platform.WEB
    tier: RiskTier = RiskTier.A
    supported_kinds: set[str] = {"fetch_profile"}

    #: Kanonik profil sayfasi — {id} placeholder
    profile_url_template: str = ""
    #: URL'den tanimlayici cikarmak icin regex (opsiyonel)
    identifier_pattern: str | None = None

    def __init__(
        self,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
        max_variant_tasks: int = 6,
    ):
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)
        self.evidence_store = evidence_store or EvidenceStore()
        self.max_variant_tasks = max_variant_tasks
        self._id_re = None
        if self.identifier_pattern:
            import re

            self._id_re = re.compile(self.identifier_pattern, re.IGNORECASE)

    # ------------------------------------------------------------------
    # Alt sinif hooks
    # ------------------------------------------------------------------

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        """Profili cek. Hesap yoksa None don."""
        raise NotImplementedError

    def entity_attributes(self, data: dict[str, Any], identifier: str) -> dict[str, Any]:
        """Entity.attributes icin alan secimi (varsayilan: bilinen anahtarlar)."""
        attrs: dict[str, Any] = {"username": identifier}
        for key in (
            "name",
            "bio",
            "location",
            "company",
            "public_email",
            "email",
            "avatar_url",
            "created_at",
            "github_link",
            "twitter_link",
            "telegram_link",
            "linkedin",
            "links",
            "social",
            "proofs",
        ):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return {k: v for k, v in attrs.items() if v is not None}

    # ------------------------------------------------------------------
    # Yardimcilar
    # ------------------------------------------------------------------

    def profile_url(self, identifier: str) -> str:
        if self.profile_url_template:
            return self.profile_url_template.format(id=identifier)
        return ""

    def identifier_from_url(self, url: str | None) -> str | None:
        if not url or not self._id_re:
            return None
        m = self._id_re.search(url)
        return m.group(1) if m else None

    def _seed_identifier(self, seed: Seed) -> str | None:
        if seed.username:
            return str(seed.username).strip().lstrip("@")
        return self.identifier_from_url(seed.url)

    # ------------------------------------------------------------------
    # Plan / Run / Normalize
    # ------------------------------------------------------------------

    def _task_for(self, params: dict[str, Any], priority: int) -> TaskSpec:
        return TaskSpec(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind="fetch_profile",
            params=params,
            priority=priority,
        )

    def _seed_tasks(self, ctx: ScanContext) -> list[TaskSpec]:
        """Seed'lerden gorevler (varsayilan: eslesen platform / URL)."""
        tasks: list[TaskSpec] = []
        seen: set[str] = set()
        for seed in ctx.seeds:
            ident: str | None = None
            if seed.platform == self.platform or (
                seed.url and self.identifier_from_url(seed.url)
            ):
                ident = self._seed_identifier(seed)
            if ident and ident not in seen:
                seen.add(ident)
                tasks.append(self._task_for({"username": ident}, priority=1))
        return tasks

    def _variant_tasks(
        self, ctx: ScanContext, existing: set[str]
    ) -> list[TaskSpec]:
        """Isim varyantlarindan gorevler (varsayilan: ilk max_variant_tasks)."""
        from showis_engine.variants import generate_variants

        variants = generate_variants(
            ctx.target.first_name, ctx.target.last_name, ctx.target.hints or {}
        )
        tasks: list[TaskSpec] = []
        for v in variants.variants:
            if len(tasks) >= self.max_variant_tasks:
                break
            if v.username in existing:
                continue
            existing.add(v.username)
            tasks.append(self._task_for({"username": v.username}, priority=4))
        return tasks

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks = self._seed_tasks(ctx)
        existing = {
            str(t.params.get("username") or t.params.get("id") or "")
            for t in tasks
        }
        tasks.extend(self._variant_tasks(ctx, existing))
        return tasks

    def _task_identifier(self, task: TaskSpec) -> str:
        return str(
            task.params.get("username")
            or task.params.get("id")
            or task.params.get("hash")
            or ""
        ).strip()

    def _obs(
        self,
        identifier: str,
        *,
        url: str | None = None,
        data: dict[str, Any] | None = None,
        evidence: list[Any] | None = None,
        error: str | None = None,
        blocked: bool = False,
    ) -> Observation:
        return Observation(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind=ObservationKind.PROFILE,
            subject=identifier,
            url=url,
            tier=self.tier,
            data=data or {},
            evidence=evidence or [],
            error=error,
            blocked=blocked,
        )

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        ident = self._task_identifier(task)
        if not ident:
            return [self._obs("", error="identifier eksik")]

        try:
            fetch = await self.fetch(ident)
        except Exception as exc:
            logger.warning("%s fetch hatasi (%s): %s", self.name, ident, exc)
            return [self._obs(ident, error=str(exc))]

        if fetch is None:
            return [
                self._obs(
                    ident,
                    data={"identifier": ident, "exists": False},
                )
            ]

        evidence: list[Any] = []
        if fetch.raw:
            evidence.append(
                self.evidence_store.store_bytes(
                    fetch.raw,
                    source_url=fetch.url,
                    kind=fetch.evidence_kind,
                    http_status=fetch.http_status,
                )
            )

        data = {
            "identifier": ident,
            "exists": True,
            "profile_url": fetch.url,
            **fetch.data,
        }
        return [self._obs(ident, url=fetch.url, data=data, evidence=evidence)]

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        entities: list[Entity] = []
        edges: list[Edge] = []
        if obs.error or obs.blocked or not obs.data:
            return entities, edges
        if obs.data.get("exists") is False:
            return entities, edges

        ident = str(obs.data.get("identifier") or obs.subject)
        ev_ids = [ev.id for ev in obs.evidence]
        entity = Entity(
            id=f"{self.platform.value}:{ident}",
            type=EntityType.ACCOUNT,
            platform=self.platform,
            label=str(obs.data.get("name") or obs.data.get("display_name") or ident),
            url=obs.data.get("profile_url") or obs.url,
            attributes=self.entity_attributes(obs.data, ident),
            evidence_ids=ev_ids,
        )
        entities.append(entity)
        return entities, edges

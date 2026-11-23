"""Site-check connector'i (Tier B).

Her kullanici-adi varyanti icin UsernameCheckEngine calistirir; bulunan
hesaplari Observation + Entity olarak uretir. Görev basina tek observation
(dagitim verisi icinde) tutulur; isabet eden sitelerin govde ornekleri
Evidence olarak baglanir.
"""

from __future__ import annotations

import logging
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
from showis_engine.http import EvidenceStore, HttpClient

from .cache import CheckCache, get_default_cache
from .dedupe import ScanPlanRegistry, get_default_registry
from .engine import EXISTS, SKIPPED, UsernameCheckEngine
from .sites import SITES, SiteSpec, select_sites

logger = logging.getLogger("showis.sitecheck.connector")


class SiteCheckConnector:
    name: str = "sitecheck"
    platform: Platform = Platform.WEB
    tier: RiskTier = RiskTier.B
    supported_kinds: set[str] = {"check_username"}
    #: Entity.attributes icindeki tespit etiketi
    detection: str = "sitecheck"

    def __init__(
        self,
        engine: UsernameCheckEngine | None = None,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
        sites: list[SiteSpec] | None = None,
        max_usernames: int = 6,
        max_evidence_per_task: int = 25,
        cache: CheckCache | None = None,
        plan_registry: ScanPlanRegistry | None = None,
    ):
        #: Onbellek: ayni (kullanici adi, site) tekrar sorgulanmasin.
        self.cache = cache if cache is not None else get_default_cache()
        self.engine = engine or UsernameCheckEngine(
            http_client=http_client, sites=sites, cache=self.cache
        )
        self.evidence_store = evidence_store or EvidenceStore()
        self.sites = sites if sites is not None else SITES
        self.max_usernames = max_usernames
        self.max_evidence_per_task = max_evidence_per_task
        #: Tarama bazli plan tekilleştirme (sitecheck + sitecheck_search).
        self.plan_registry = (
            plan_registry if plan_registry is not None else get_default_registry()
        )

    def sites_for_mode(self, mode: str | None) -> list[SiteSpec]:
        """Moda göre kapsam: O1 çekirdek, O2/O3 genişleyen WMN kuyruğu."""
        if self.sites is SITES:
            return select_sites(mode)
        # Test/özel connector'lar kendi listesini kullanır.
        return self.sites

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def _make_task(self, username: str, priority: int = 6) -> TaskSpec:
        return TaskSpec(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind="check_username",
            params={"username": username},
            priority=priority,
        )

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seen: set[str] = set()

        def add_task(uname: str | None, priority: int) -> None:
            """Tekilleştirme: plan ici + tarama geneli (connector'lar arasi)."""
            if not uname or uname in seen:
                return
            seen.add(uname)
            if not self.plan_registry.claim(ctx.scan_id, uname, self.name):
                logger.debug(
                    "Site-check tekilleştirme: %r bu taramada başka connector'da planlandı",
                    uname,
                )
                return
            tasks.append(self._make_task(uname, priority=priority))

        # 1) Seed kullanici adlari — platform farketmez, hepsi taranir
        for seed in ctx.seeds:
            add_task(self._seed_username(seed), priority=2)

        # 2) Hedef ad/soyad varyantilari — toplam görev sayisi
        #    max_usernames + seed sayisi ile sinirli
        from showis_engine.variants import generate_variants

        variants = generate_variants(
            ctx.target.first_name,
            ctx.target.last_name,
            ctx.target.hints or {},
            nickname=ctx.target.nickname,
        )
        budget = self.max_usernames
        for v in variants.variants:
            if budget <= 0:
                break
            if v.username in seen:
                continue
            before = len(tasks)
            add_task(v.username, priority=4)
            if len(tasks) > before:
                budget -= 1

        return tasks

    @staticmethod
    def _seed_username(seed: Seed) -> str | None:
        if seed.username:
            return str(seed.username).strip().lstrip("@")
        return None

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        username = str(task.params.get("username") or "").strip().lstrip("@")
        if not username:
            return [
                Observation(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=self.platform,
                    kind=ObservationKind.SEARCH_RESULT,
                    subject="",
                    tier=self.tier,
                    data={},
                    error="username parametresi eksik",
                )
            ]

        results = await self.engine.check_username(username, self.sites_for_mode(ctx.mode))

        hits = [r for r in results if r.exists]
        cache_hits = sum(1 for r in results if r.from_cache)
        evidence: list[Any] = []
        checks_payload: list[dict[str, Any]] = []

        for r in results:
            entry: dict[str, Any] = {
                "site": r.site.key,
                "name": r.site.name,
                "platform": r.site.platform.value,
                "status": r.status,
                "url": r.final_url or r.url,
                "http_status": r.http_status,
            }
            if r.status == EXISTS:
                entry["confidence"] = self._confidence(r.site)
            checks_payload.append(entry)

        for r in hits[: self.max_evidence_per_task]:
            if r.body_sample:
                kind = "json" if r.site.json_path is not None else "html"
                ev = self.evidence_store.store_bytes(
                    r.body_sample,
                    source_url=r.final_url or r.url,
                    kind=kind,
                    http_status=r.http_status,
                    user_agent=self.engine.http.user_agent,
                )
                evidence.append(ev)
                r_ev_id = ev.id
            else:
                r_ev_id = None
            # evidence id'sini ilgili check kaydina bagla
            for entry in checks_payload:
                if entry["site"] == r.site.key and entry["status"] == EXISTS:
                    if r_ev_id:
                        entry["evidence_id"] = r_ev_id
                    break

        obs = Observation(
            id=str(uuid.uuid4()),
            connector=self.name,
            platform=self.platform,
            kind=ObservationKind.PROFILE,
            subject=username,
            url=None,
            tier=self.tier,
            data={
                "username": username,
                "total_sites": len(results),
                "hits": len(hits),
                "checks": checks_payload,
                "skipped": sum(1 for r in results if r.status == SKIPPED),
                "errors": sum(1 for r in results if r.status == "error"),
                #: Onbellekten gelen (yeni ag istegi atilmayan) site sayisi
                "cache_hits": cache_hits,
                "network_checks": len(results) - cache_hits,
            },
            evidence=evidence,
        )
        return [obs]

    # ------------------------------------------------------------------
    # Normalize
    # ------------------------------------------------------------------

    @staticmethod
    def _confidence(spec: SiteSpec) -> float:
        # Generic HTML kontrolu artik govdede kullanici adi dogrulamasi ister
        # (0.65). Bunu kapatmis istisnalar daha dusuk guvenle isaretlenir.
        base = 0.65 if spec.require_username else 0.5
        if spec.json_path is not None:
            base = 0.8
        if spec.present_regex or spec.absent_regex:
            base = max(base, 0.75)
        return min(base, 0.9)

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        entities: list[Entity] = []
        edges: list[Edge] = []
        if obs.error or obs.blocked or not obs.data:
            return entities, edges

        username = str(obs.data.get("username") or obs.subject)
        ev_ids = [ev.id for ev in obs.evidence]
        ev_by_id = {ev.id: ev for ev in obs.evidence}

        for check in obs.data.get("checks") or []:
            if check.get("status") != EXISTS:
                continue
            site_key = check.get("site")
            try:
                platform = Platform(str(check.get("platform")))
            except ValueError:
                platform = Platform.UNKNOWN

            entity_evs = ev_ids[:]
            ev_id = check.get("evidence_id")
            if ev_id and ev_id in ev_by_id:
                entity_evs = [ev_id]

            spec = next((item for item in self.sites if item.key == site_key), None)
            identifier = (
                f"{spec.id_namespace}:{username}"
                if spec is not None and spec.id_namespace
                else str(username)
            )
            label = (
                f"{username} · {spec.name}"
                if spec is not None and spec.id_namespace
                else username
            )
            entities.append(
                Entity(
                    id=make_entity_id(platform.value, identifier),
                    type=EntityType.ACCOUNT,
                    platform=platform,
                    label=label,
                    url=check.get("url"),
                    attributes={
                        "username": username,
                        "site": site_key,
                        "site_name": check.get("name"),
                        "detection": self.detection,
                        "confidence_hint": check.get("confidence"),
                        "id_namespace": spec.id_namespace if spec else None,
                    },
                    evidence_ids=entity_evs,
                )
            )

        return entities, edges

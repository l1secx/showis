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

from .budget import primary_site_limit
from .cache import CheckCache, get_default_cache
from .dedupe import ScanPlanRegistry, get_default_registry
from .engine import EXISTS, SKIPPED, UsernameCheckEngine
from .sites import SITES, SiteSpec
from .wmn import sites_for_username

logger = logging.getLogger("showis.sitecheck.connector")

#: Sitecheck planinin uretecegi en fazla kullanici adi (D10: butce korumasi).
#: Tavan olmadan site basina limit dusuk kalir ve elle yazilmis kapsam kesilir.
MAX_PLANNED_USERNAMES = 10


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
        #: Testler/acik cagrilar kendi listesini verdiyse mod bazli site
        #: secimi (WMN) devre disi kalir; davranis degismez.
        self._explicit_sites = sites is not None
        self.max_usernames = max_usernames
        #: Tarama modu (scanner plan oncesinde atar: O1/O2/O3). None -> ctx/O2.
        #: Istek butcesi bu moda gore secilir (D2).
        self.scan_mode: str | None = None
        self.max_evidence_per_task = max_evidence_per_task
        #: Tarama bazli plan tekilleştirme (sitecheck + sitecheck_search).
        self.plan_registry = (
            plan_registry if plan_registry is not None else get_default_registry()
        )

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

    @staticmethod
    def _plan_mode(ctx: ScanContext) -> str:
        """Planlama modu: ``connector.scan_mode`` > ``ctx.mode`` > ``"O2"``.

        ``ScanContext`` semasinda ``mode`` alani yok; scanner modu connector'a
        ``scan_mode`` niteligiyle verebilir (``max_variant_tasks`` ile ayni
        desen). Verilmezse O2 butcesi (en muhafazakar) kullanilir.
        """
        ctx_mode = str(getattr(ctx, "mode", "") or "").strip()
        return ctx_mode or "O2"

    def _resolve_plan_mode(self, ctx: ScanContext) -> str:
        override = str(getattr(self, "scan_mode", "") or "").strip()
        return override or self._plan_mode(ctx)

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        mode = self._resolve_plan_mode(ctx)
        tasks: list[TaskSpec] = []
        seen: set[str] = set()

        def add_task(uname: str | None, priority: int) -> None:
            """Tekilleştirme: plan ici + tarama geneli (connector'lar arasi)."""
            if not uname or uname in seen:
                return
            if len(tasks) >= MAX_PLANNED_USERNAMES:
                if uname not in seen:
                    logger.debug(
                        "Site-check kullanıcı adı tavanı (%d) doldu: %r atlandı",
                        MAX_PLANNED_USERNAMES,
                        uname,
                    )
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

        # D10: AILE butcesinin birincil payi kullanici adi sayisina bolunur ->
        # gorev basina site limiti. n x site_limit <= pay (arama connector'i
        # ayni aileden %20'lik payi kullanir; toplam aile butcesini asmaz).
        site_limit = primary_site_limit(mode, len(tasks))
        for task in tasks:
            task.params["mode"] = mode
            task.params["site_limit"] = site_limit
        if len(tasks) and site_limit:
            logger.info(
                "Site-check plani: %s modu, %d kullanıcı adı × %d site (bütçe payı)",
                mode,
                len(tasks),
                site_limit,
            )
        return tasks

    @staticmethod
    def _seed_username(seed: Seed) -> str | None:
        if seed.username:
            return str(seed.username).strip().lstrip("@")
        return None

    @staticmethod
    def _scan_mode(task: TaskSpec, ctx: ScanContext) -> str:
        """Tarama modu: ``task.params["mode"]`` > ``ctx.mode`` > ``"O2"``."""
        param_mode = str(task.params.get("mode") or "").strip()
        if param_mode:
            return param_mode
        ctx_mode = str(getattr(ctx, "mode", "") or "").strip()
        return ctx_mode or "O2"

    def _targets(self, username: str, task: TaskSpec, ctx: ScanContext) -> list[SiteSpec]:
        """Bu kullanıcı adı için taranacak siteler (bütçe payı + rotasyon).

        ``sites=`` ile açık liste verilmişse (testler, elle çağrılar) o liste
        kullanılır — WMN dilimi devreye girmez.
        """
        if self._explicit_sites:
            return self.sites
        mode = self._scan_mode(task, ctx)
        return sites_for_username(username, mode, self._site_limit(task, mode))

    @staticmethod
    def _site_limit(task: TaskSpec, mode: str | None = None) -> int:
        """Görevdeki ``site_limit``; yoksa/geçersizse **fail-safe** tavan.

        Eski (limitsiz) DB görevleri için tam bütçe kullanılmaz: tavan
        ``MAX_PLANNED_USERNAMES`` varsayımıyla birincil paydan hesaplanır
        (Z3 C1: limitsiz görev 592 site tarıyordu).
        """
        raw = task.params.get("site_limit")
        if raw is None:
            return primary_site_limit(mode, MAX_PLANNED_USERNAMES)
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Geçersiz site_limit (%r), fail-safe limit kullanılacak", raw
            )
            return primary_site_limit(mode, MAX_PLANNED_USERNAMES)

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

        mode = self._scan_mode(task, ctx)
        targets = self._targets(username, task, ctx)
        results = await self.engine.check_username(username, targets)

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
                #: mod bazli plan: hangi modda kaç site tarandı (butce kaniti)
                "scan_mode": mode,
                "sites_planned": len(targets),
                #: plan aşamasında bütçeden türetilen kullanıcı adı başına site tavanı
                "site_limit": self._site_limit(task, mode),
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
        # json/status tabanli kontroller daha guvenilir
        base = 0.6
        if spec.json_path is not None:
            base += 0.2
        if spec.present_regex or spec.absent_regex:
            base += 0.1
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

            entities.append(
                Entity(
                    id=make_entity_id(platform.value, str(username)),
                    type=EntityType.ACCOUNT,
                    platform=platform,
                    label=username,
                    url=check.get("url"),
                    attributes={
                        "username": username,
                        "site": site_key,
                        "site_name": check.get("name"),
                        "detection": self.detection,
                        "confidence_hint": check.get("confidence"),
                    },
                    evidence_ids=entity_evs,
                )
            )

        return entities, edges

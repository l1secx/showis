import asyncio
import logging
import os
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from showis.db.models import (
    AuditLog,
    Case,
    Scan,
)
from showis.db.models import (
    Edge as DbEdge,
)
from showis.db.models import (
    Entity as DbEntity,
)
from showis.db.models import (
    Evidence as DbEvidence,
)
from showis.db.models import (
    Finding as DbFinding,
)
from showis.db.models import (
    Observation as DbObservation,
)
from showis.db.models import (
    Seed as DbSeed,
)
from showis.db.models import (
    Target as DbTarget,
)
from showis.db.models import (
    Task as DbTask,
)
from showis.services.audit import record_audit
from showis.services.orchestrator import event_broadcaster
from showis_connectors.websearch.normalize import extract_platform_from_url
from showis_contracts import (
    Budget,
    Edge,
    Entity,
    EntityType,
    Observation,
    Platform,
    ScanContext,
    Seed,
    Target,
    TaskSpec,
)
from showis_engine.authenticity import compute_authenticity
from showis_engine.http import (
    BudgetExceededError,
    count_requests_scope,
    reset_request_budget,
    set_request_budget,
)
from showis_engine.identity_edges import (
    build_identity_edges,
    db_safe_edge_id,
    edges_by_type,
    pair_key,
)
from showis_engine.links import (
    build_avatar_match_edges,
    build_cross_link_edges,
    fetch_avatar_hashes,
    hamming_distance,
)
from showis_engine.llm_explainer import FindingExplainer
from showis_engine.planner import QueryPlanner
from showis_engine.policy import PolicyViolationError, enforce_tier
from showis_engine.resolver import Resolver
from showis_engine.scan_modes import skipped_connectors, task_allowed, variant_cap
from showis_engine.variants import generate_variants
from showis_llm import LLMClient
from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.scanner")

#: Tek taramada LLM ile aciklanacak azami bulgu (her cagri ~6 sn)
_MAX_LLM_EXPLANATIONS = 12

#: Paralel gorev yurutme varsayilani / sinirlari.
#: `SHOWIS_SCAN_CONCURRENCY=1` eski sirali davranisi birebir korur.
_DEFAULT_SCAN_CONCURRENCY = 4
_MIN_SCAN_CONCURRENCY = 1
_MAX_SCAN_CONCURRENCY = 8


def scan_concurrency(explicit: int | None = None) -> int:
    """Gorev es zamanlilik sinirini cozer (acik deger > env > varsayilan, 1..8)."""
    if explicit is not None:
        value = explicit
    else:
        raw = os.getenv("SHOWIS_SCAN_CONCURRENCY", str(_DEFAULT_SCAN_CONCURRENCY))
        try:
            value = int(str(raw).strip())
        except ValueError:
            logger.warning("Gecersiz SHOWIS_SCAN_CONCURRENCY=%r, varsayilan kullanilir", raw)
            value = _DEFAULT_SCAN_CONCURRENCY
    return max(_MIN_SCAN_CONCURRENCY, min(_MAX_SCAN_CONCURRENCY, value))


@dataclass
class _TaskOutcome:
    """Paralel yurutulen tek gorevin sonucu (DB'ye yazilmadan onceki tasiyici)."""

    task_id: str
    observations: list[Observation] = field(default_factory=list)
    requests: int = 0
    error: Exception | None = None
    #: HTTP butcesi tukendigi icin gorev yarida kesildi (fail-closed)
    budget_exceeded: bool = False


@dataclass
class _TaskRunStats:
    """Gorev yurutme dongusunun ozeti."""

    executed: int = 0
    http_requests: int = 0
    total_observations: int = 0
    budget_hit: str | None = None
    cancelled: bool = False
    cancelled_tasks: int = 0


def _entity_username(entity: Entity) -> str:
    """Entity icin normalize kullanici adi (karsilastirma icin)."""
    ident = entity.attributes.get("username") or entity.attributes.get("login")
    if not ident:
        ident = entity.label
    return str(ident).lstrip("@").strip().lower()


def _safe_platform(value: str) -> Platform:
    try:
        return Platform(value)
    except ValueError:
        return Platform.UNKNOWN


def _merge_entity(dst: Entity, src: Entity) -> None:
    """Ayni entity icin zengin nitelikleri birlestirir."""
    for key, value in (src.attributes or {}).items():
        if value is not None and (key not in dst.attributes or dst.attributes[key] is None):
            dst.attributes[key] = value
    dst.evidence_ids = list(dict.fromkeys([*dst.evidence_ids, *src.evidence_ids]))
    if src.url and not dst.url:
        dst.url = src.url


class ScannerService:
    #: Surec ici iptal kanali: scan_id -> asyncio.Event.
    #: `POST /scans/{id}/cancel` bunu tetikler; calisan dongu event'i gorunce
    #: yeni gorev dispatch etmez ve ucustaki gorevleri `CancelledError` ile iptal eder.
    _cancel_events: dict[str, asyncio.Event] = {}

    def __init__(
        self,
        connectors: list[Any] | None = None,
        llm_client: LLMClient | None = None,
        concurrency: int | None = None,
    ):
        self.connectors = connectors or []
        self.llm_client = llm_client
        #: None -> `SHOWIS_SCAN_CONCURRENCY` (varsayilan 4) kullanilir.
        self.concurrency = concurrency
        self.resolver = Resolver()
        self.explainer = FindingExplainer(llm_client=llm_client)
        # LLM'siz anlatici: artimli gecislerde hizli yerel aciklama uretir
        self.local_explainer = FindingExplainer(llm_client=None)
        self.planner = QueryPlanner(llm_client=llm_client)

    # ------------------------------------------------------------------
    # Iptal kanali (sinif duzeyi — API ve calisan dongu ayni surecte)
    # ------------------------------------------------------------------

    @classmethod
    def cancel(cls, scan_id: str) -> None:
        """Taramayi iptal sinyaliyle isaretler (yeni dispatch yok, ucanlar kesilir)."""
        cls._cancel_events.setdefault(scan_id, asyncio.Event()).set()

    @classmethod
    def cancel_event(cls, scan_id: str) -> asyncio.Event:
        """Tarama icin iptal event'ini dondurur (yoksa olusturur)."""
        return cls._cancel_events.setdefault(scan_id, asyncio.Event())

    @classmethod
    def is_cancelled(cls, scan_id: str) -> bool:
        event = cls._cancel_events.get(scan_id)
        return bool(event is not None and event.is_set())

    @classmethod
    def clear_cancel(cls, scan_id: str) -> None:
        """Iptal isaretini temizler (tarama bitisi / testler)."""
        cls._cancel_events.pop(scan_id, None)

    async def _scan_is_cancelled(self, scan_id: str, db: AsyncSession) -> bool:
        """Iptal kontrolu: once surec ici kanal, sonra DB'deki guncel durum.

        `expire_on_commit=False` oldugu icin bellekteki ORM ornegine guvenilmez;
        durum her seferinde `SELECT scans.status` ile tazelenir.
        """
        if self.is_cancelled(scan_id):
            return True
        status = (
            await db.execute(select(Scan.status).where(Scan.id == scan_id))
        ).scalar_one_or_none()
        return status == "cancelled"

    async def execute_scan(self, scan_id: str, db: AsyncSession) -> None:
        """Scan sürecini baştan sona yönetir."""
        try:
            await self._run_scan(scan_id, db)
        except Exception:
            logger.exception("Scan %s beklenmeyen hatayla sonlandi", scan_id)
            try:
                # Bozuk session'da execute PendingRollbackError verir; once temizle
                await db.rollback()
                res = await db.execute(select(Scan).where(Scan.id == scan_id))
                scan = res.scalar_one_or_none()
                if scan and scan.status in ("queued", "running"):
                    scan.status = "failed"
                    scan.finished_at = datetime.now(timezone.utc)
                    scan.stats = {**(scan.stats or {}), "error": "unexpected_failure"}
                    await db.commit()
                    await event_broadcaster.broadcast(
                        scan.id, "scan.failed", {"status": "failed", "error": "unexpected_failure"}
                    )
            except Exception:
                logger.exception("Scan %s hata durumu yazilamadi", scan_id)

    async def _run_scan(self, scan_id: str, db: AsyncSession) -> None:
        res = await db.execute(select(Scan).where(Scan.id == scan_id))
        scan = res.scalar_one_or_none()
        if not scan:
            logger.error("Scan bulunamadı: %s", scan_id)
            return

        case_res = await db.execute(select(Case).where(Case.id == scan.case_id))
        case = case_res.scalar_one_or_none()
        if not case:
            return

        target_res = await db.execute(select(DbTarget).where(DbTarget.case_id == case.id))
        db_target = target_res.scalar_one_or_none()
        if not db_target:
            return

        # Prepare contracts
        target = Target(
            first_name=db_target.first_name,
            last_name=db_target.last_name,
            hints=db_target.hints or {},
            review_required=db_target.review_required,
        )
        seeds_res = await db.execute(select(DbSeed).where(DbSeed.case_id == case.id))
        db_seeds = seeds_res.scalars().all()
        def _seed_platform(value: str) -> Platform:
            try:
                return Platform(value)
            except ValueError:
                return Platform.UNKNOWN

        seeds = [
            Seed(
                platform=_seed_platform(s.platform),
                url=s.url,
                username=s.username,
                email=s.email,
                source=s.source,
            )
            for s in db_seeds
        ]
        budget = Budget(**(scan.budget or {}))
        ctx = ScanContext(
            case_id=case.id,
            scan_id=scan.id,
            target=target,
            seeds=seeds,
            budget=budget,
            llm_session_id=str(uuid.uuid4()),
        )

        # Update Scan to RUNNING — kosullu UPDATE: es zamanli iptal ezilmesin
        started_at = datetime.now(timezone.utc)
        transition = cast(
            CursorResult[Any],
            await db.execute(
                update(Scan)
                .where(Scan.id == scan.id, Scan.status.in_(("queued", "running")))
                .values(status="running", started_at=started_at)
                .execution_options(synchronize_session=False)
            ),
        )
        await db.commit()
        await db.refresh(scan)
        if not transition.rowcount:
            # Arada iptal edildi (veya kapatildi): gorev uretmeden cik
            logger.info("Scan %s baslatilmadi (durum: %s)", scan.id, scan.status)
            await event_broadcaster.broadcast(
                scan.id, "scan.cancelled", {"status": "cancelled"}
            )
            return
        await event_broadcaster.broadcast(scan.id, "scan.status", {"status": "running"})

        # Step 1: Variants & Planning
        variants = generate_variants(target.first_name, target.last_name, target.hints)
        await event_broadcaster.broadcast(
            scan.id,
            "log",
            {"message": f"{len(variants.variants)} kullanıcı adı varyantı üretildi."},
        )

        try:
            query_plan = await self.planner.plan(target, variants)
            if self.llm_client:
                msg = (
                    f"LLM dinamik sorgu planlayıcı devrede: "
                    f"{len(query_plan.queries)} hedefli sorgu üretildi."
                )
                await event_broadcaster.broadcast(scan.id, "log", {"message": msg})
        except Exception as exc:
            logger.warning("Query planlama hatası: %s", exc)

        # Gather tasks from connectors (mod O1 hizli: yavas site-check atlanir)
        # Varyant sayisi da moda gore sinirlanir; aksi halde O1 de dakikalarca surer.
        cap = variant_cap(scan.mode)
        for conn in self.connectors:
            if hasattr(conn, "max_variant_tasks"):
                conn.max_variant_tasks = min(conn.max_variant_tasks, cap)

        all_task_specs: list[TaskSpec] = []
        for conn in self.connectors:
            if not task_allowed(scan.mode, conn.name):
                continue
            try:
                specs = await conn.plan(ctx)
                all_task_specs.extend(specs)
            except Exception as e:
                logger.error("Connector plan error (%s): %s", conn.name, e)
        missing = skipped_connectors(scan.mode, [c.name for c in self.connectors])
        if missing:
            await event_broadcaster.broadcast(
                scan.id,
                "log",
                {"message": f"{scan.mode} modunda atlanan connector'lar: {', '.join(missing)}"},
            )

        # Record tasks in DB
        db_tasks: list[DbTask] = []
        for spec in all_task_specs:
            t = DbTask(
                id=spec.id,
                scan_id=scan.id,
                connector=spec.connector,
                platform=spec.platform.value,
                kind=spec.kind,
                params=spec.params,
                status="pending",
            )
            db.add(t)
            db_tasks.append(t)
        await db.commit()

        # Step 2: Execute tasks with budget check
        # Paralel yurutme (SHOWIS_SCAN_CONCURRENCY): connector.run() gorevleri es
        # zamanli calisir; TUM DB yazimlari ve SSE yayinlari ana coroutine'de
        # SIRALI yapilir (AsyncSession es zamanli paylasilmaz -> tek yazar).
        entity_map: dict[str, Entity] = {}
        edge_map: dict[str, Edge] = {}
        seed_emails = {s.email.lower() for s in seeds if s.email}

        concurrency = scan_concurrency(self.concurrency)
        if concurrency > 1:
            await event_broadcaster.broadcast(
                scan.id,
                "log",
                {"message": f"Görevler {concurrency} eşzamanlı işlenecek."},
            )

        run_stats = await self._execute_tasks(
            db=db,
            scan=scan,
            case=case,
            ctx=ctx,
            target=target,
            budget=budget,
            concurrency=concurrency,
            entity_map=entity_map,
            edge_map=edge_map,
            seed_emails=seed_emails,
        )
        executed_tasks_count = run_stats.executed
        http_requests_count = run_stats.http_requests
        total_observations = run_stats.total_observations
        budget_hit = run_stats.budget_hit

        if budget_hit:
            messages = {
                "max_tasks": "Bütçe limiti (max_tasks) aşıldı, kalan görevler atlandı.",
                "max_http_requests": "Bütçe limiti (max_http_requests) aşıldı.",
                "max_wall_seconds": (
                    f"Duvar saati bütçesi ({budget.max_wall_seconds}s) doldu, "
                    "kalan görevler atlandı."
                ),
            }
            await event_broadcaster.broadcast(
                scan.id, "log", {"message": messages[budget_hit]}
            )

        # Calistirilamayan gorevler "pending" kalmasin (UI'da asili gorunuyordu)
        leftover = (
            await db.execute(
                select(DbTask).where(DbTask.scan_id == scan.id, DbTask.status == "pending")
            )
        ).scalars().all()
        for task in leftover:
            task.status = "skipped"
            task.error = task.error or "budget_or_timeout"
        if leftover:
            await event_broadcaster.broadcast(
                scan.id,
                "log",
                {"message": f"{len(leftover)} görev bütçe/zaman nedeniyle atlandı."},
            )

        all_entities = list(entity_map.values())

        # Step 2a: Avatar hash'leri (SHA-256 + Perceptual dHash)
        avatar_candidates = [
            ent
            for ent in all_entities
            if (ent.attributes or {}).get("avatar_url")
            and not (ent.attributes or {}).get("avatar_sha256")
        ][:20]
        if avatar_candidates:
            avatar_sem = asyncio.Semaphore(5)
            avatar_fetched = [0]

            async def _hash_avatar(ent: Entity) -> None:
                async with avatar_sem:
                    sha, dhash = await fetch_avatar_hashes(ent.attributes.get("avatar_url"))
                    if sha:
                        ent.attributes["avatar_sha256"] = sha
                    if dhash:
                        ent.attributes["avatar_dhash"] = dhash
                    if sha or dhash:
                        avatar_fetched[0] += 1

            await asyncio.gather(*(_hash_avatar(e) for e in avatar_candidates))
            http_requests_count += avatar_fetched[0]

        # Step 2a2: Kimlik edge'leri (T-309) — ayni kullanici adi (yildiz
        # topolojisi), avatar SHA-256/dHash, e-posta/PGP ve bio CROSS_LINK.
        # Dedupe (source,target,type); id'ler DB VARCHAR(36) sinirina sigdirilir.
        identity_edges = build_identity_edges(all_entities)
        seen_pairs = {pair_key(edge) for edge in identity_edges}
        for edge in identity_edges:
            edge_map[edge.id] = edge
        # Mevcut avatar eslesmesi korunur; kimlik modulunun urettigi ciftler tekrar eklenmez.
        for ent in all_entities:
            for edge in build_cross_link_edges(ent, all_entities):
                if pair_key(edge) in seen_pairs:
                    continue
                seen_pairs.add(pair_key(edge))
                edge_map[db_safe_edge_id(edge.id)] = edge
        for edge in build_avatar_match_edges(all_entities):
            if pair_key(edge) in seen_pairs:
                continue
            seen_pairs.add(pair_key(edge))
            edge_map[db_safe_edge_id(edge.id)] = edge

        # Step 2b: Entity'leri DB'ye toplu yaz + edge'leri persist et
        known_entity_ids = set(entity_map.keys())
        await self._persist_entities(db, case, all_entities)

        # Edge endpoint'i eksikse "referans" stub entity olustur (bulgu uretmez)
        async def _ensure_stub(entity_id: str) -> None:
            if entity_id in known_entity_ids:
                return
            known_entity_ids.add(entity_id)
            platform_value, _, ident = entity_id.partition(":")
            try:
                platform = Platform(platform_value)
            except ValueError:
                platform = Platform.UNKNOWN
            await db.merge(
                DbEntity(
                    id=entity_id,
                    case_id=case.id,
                    type=EntityType.ACCOUNT.value,
                    platform=platform.value,
                    label=ident or entity_id,
                    attributes={"unverified_ref": True},
                )
            )

        for edge in edge_map.values():
            await _ensure_stub(edge.source_entity_id)
            await _ensure_stub(edge.target_entity_id)
            await db.merge(
                DbEdge(
                    id=edge.id,
                    case_id=case.id,
                    type=edge.type.value,
                    source_entity_id=edge.source_entity_id,
                    target_entity_id=edge.target_entity_id,
                    confidence=edge.confidence,
                    reason=edge.reason,
                    evidence_ids=edge.evidence_ids,
                )
            )
        await db.commit()

        await self._write_findings(db, scan, case, target, list(entity_map.values()), seed_emails)

        # Final durum: KOSULLU UPDATE — DB'de `cancelled` yazildiysa `completed`
        # ile ezilmez (SEC-06). Istatistikler her durumda yazilir.
        stats_payload = {
            "executed_tasks": executed_tasks_count,
            "http_requests": http_requests_count,
            "total_observations": total_observations,
            "total_entities": len(all_entities),
            "total_edges": len(edge_map),
            "edges_by_type": edges_by_type(list(edge_map.values())),
            "total_findings": len(all_entities),
            "skipped_tasks": len(leftover),
            "cancelled_tasks": run_stats.cancelled_tasks,
        }
        finished_at = datetime.now(timezone.utc)
        await db.execute(
            update(Scan)
            .where(Scan.id == scan.id, Scan.status == "running")
            .values(status="completed", finished_at=finished_at)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(Scan)
            .where(Scan.id == scan.id)
            .values(stats=stats_payload, finished_at=finished_at)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        final_status = (
            await db.execute(select(Scan.status).where(Scan.id == scan.id))
        ).scalar_one_or_none() or "cancelled"
        self.clear_cancel(scan.id)

        # Audit: yalniz gercekten tamamlanan tarama `scan.completed` yazar
        await record_audit(
            db=db,
            action="scan.completed" if final_status == "completed" else "scan.cancelled",
            case_id=case.id,
            target=f"Scan: {scan.id}",
            details=stats_payload,
        )

        await event_broadcaster.broadcast(
            scan.id,
            f"scan.{final_status}",
            {
                "status": final_status,
                "stats": stats_payload,
            },
        )

    async def _run_task(
        self, connector: Any, spec: TaskSpec, ctx: ScanContext
    ) -> _TaskOutcome:
        """Tek bir gorevi calistirir; hata taramayi durdurmaz (gorev izole).

        Istek sayimi `count_requests_scope()` ile gorev baglaminda yapilir:
        surec-global sayac paralelde baska gorevlerin isteklerini de gorurdu.
        """
        with count_requests_scope() as scope:
            try:
                observations = await connector.run(spec, ctx)
            except BudgetExceededError as exc:
                # SEC-09: istek atilmadan kesildi; gorev failed, tarama devam eder
                logger.info("Gorev butce nedeniyle kesildi (%s): %s", spec.id, exc)
                return _TaskOutcome(
                    task_id=spec.id,
                    requests=scope.count,
                    error=exc,
                    budget_exceeded=True,
                )
            except Exception as exc:  # connector hatasi -> task.failed
                logger.warning(
                    "Gorev basarisiz (%s/%s): %s", spec.connector, spec.id, exc
                )
                return _TaskOutcome(task_id=spec.id, requests=scope.count, error=exc)
            return _TaskOutcome(
                task_id=spec.id,
                observations=list(observations or []),
                requests=scope.count,
            )

    async def _execute_tasks(
        self,
        *,
        db: AsyncSession,
        scan: Scan,
        case: Case,
        ctx: ScanContext,
        target: Target,
        budget: Budget,
        concurrency: int,
        entity_map: dict[str, Entity],
        edge_map: dict[str, Edge],
        seed_emails: set[str],
    ) -> _TaskRunStats:
        """Gorevleri sinirli es zamanlilikla yurutur, sonuclari SIRALI yazar.

        Mimari sozlesme:
        * `connector.run()` es zamanli (en fazla `concurrency` gorev ucar),
        * DB yazimlari / SSE yayinlari yalnizca bu coroutine'de, tek tek,
          tamamlanma sirasina gore yapilir (`AsyncSession` paylasilmaz),
        * butce kontrolleri her dispatch oncesi yapilir; bu yuzden `max_tasks`
          asimi olmaz, `max_http_requests` icin en fazla `concurrency` gorev
          kadar asim olabilir (istek sayisi ancak gorev bitince bilinir),
        * iptalde yeni gorev dispatch edilmez, calisanlar tamamlanir.
        """
        stats = _TaskRunStats()
        scan_id = scan.id
        started_at = scan.started_at
        # Drain loop: connector'lar calisirken yeni pending gorev ekleyebilir
        # (sitecheck_search aramada bulunan kullanici adlarini gorev yapar).
        seen_task_ids: set[str] = set()
        pending: deque[DbTask] = deque()
        inflight: dict[asyncio.Task[_TaskOutcome], tuple[DbTask, TaskSpec, Any]] = {}

        def _elapsed() -> float:
            if not started_at:
                return 0.0
            # SQLite aware bilgiyi saklamaz; karsilastirma icin normalize et
            origin = started_at
            if origin.tzinfo is None:
                origin = origin.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - origin).total_seconds()

        async def _refill_pending() -> None:
            rows = (
                await db.execute(
                    select(DbTask)
                    .where(DbTask.scan_id == scan_id, DbTask.status == "pending")
                    .order_by(DbTask.created_at, DbTask.id)
                )
            ).scalars().all()
            for db_task in rows:
                if db_task.id not in seen_task_ids:
                    seen_task_ids.add(db_task.id)
                    pending.append(db_task)

        async def _is_cancelled() -> bool:
            return await self._scan_is_cancelled(scan_id, db)

        def _budget_reason() -> str | None:
            # Ucan gorevler de sayilir: `max_tasks` asimi olmaz.
            if stats.executed + len(inflight) >= budget.max_tasks:
                return "max_tasks"
            if stats.http_requests >= budget.max_http_requests:
                return "max_http_requests"
            if budget.max_wall_seconds and _elapsed() >= budget.max_wall_seconds:
                return "max_wall_seconds"
            return None

        async def _dispatch() -> str | None:
            """Bekleyen gorevleri tier kontrolunden gecirip baslatir."""
            while pending and len(inflight) < concurrency:
                reason = _budget_reason()
                if reason is not None:
                    return reason
                db_task = pending.popleft()
                spec = TaskSpec(
                    id=db_task.id,
                    connector=db_task.connector,
                    platform=_safe_platform(db_task.platform),
                    kind=db_task.kind,
                    params=db_task.params or {},
                )
                matched = next(
                    (c for c in self.connectors if c.name == spec.connector), None
                )
                if not matched:
                    continue

                # Tier C connector'lar yalnizca arama-turevi gorev uretebilir
                conn_tier = getattr(matched, "tier", None)
                if conn_tier is not None:
                    try:
                        enforce_tier(spec, conn_tier)
                    except PolicyViolationError as exc:
                        logger.warning("Tier ihlali atlandi (%s): %s", spec.id, exc)
                        continue

                db_task.status = "running"
                # Gercek yurutme baslangici (T-102 kalibrasyonu: kuyruk beklemesi
                # `created_at` ile `started_at` arasindaki farktir).
                db_task.started_at = datetime.now(timezone.utc)
                await db.commit()
                await event_broadcaster.broadcast(
                    scan_id,
                    "task.started",
                    {
                        "task_id": db_task.id,
                        "kind": db_task.kind,
                        "connector": db_task.connector,
                    },
                )
                inflight[asyncio.create_task(self._run_task(matched, spec, ctx))] = (
                    db_task,
                    spec,
                    matched,
                )
            return None

        async def _persist(
            db_task: DbTask, spec: TaskSpec, connector: Any, outcome: _TaskOutcome
        ) -> None:
            """Gorev sonucunu DB'ye ve SSE'ye SIRALI olarak yazar (tek yazar)."""
            stats.executed += 1
            now = datetime.now(timezone.utc)

            if outcome.error is not None:
                stats.http_requests += outcome.requests
                db_task.status = "failed"
                db_task.error = str(outcome.error)
                db_task.finished_at = now
                await db.commit()
                if outcome.budget_exceeded:
                    # Kalan gorevler dispatch edilmesin (fail-closed butce)
                    stats.budget_hit = "max_http_requests"
                await event_broadcaster.broadcast(
                    scan_id, "task.failed", {"task_id": db_task.id, "error": str(outcome.error)}
                )
                return

            obs_list = outcome.observations
            # Butce gercek istek sayisini sayar (bir gorev ~35 istek atabilir)
            stats.http_requests += max(1, outcome.requests)
            db_task.status = "done"
            db_task.observation_count = len(obs_list)
            db_task.finished_at = now

            # SEC6: her dis istek gorevi audit'e yazilir
            db.add(
                AuditLog(
                    case_id=case.id,
                    actor="scanner",
                    action="connector.task",
                    target=f"{spec.connector}:{spec.kind}",
                    details={
                        "task_id": spec.id,
                        "params_keys": sorted(spec.params.keys()),
                        "observations": len(obs_list),
                        "blocked": sum(1 for o in obs_list if o.blocked),
                    },
                )
            )

            for obs in obs_list:
                # Kanitlari DB'ye yaz (expires_at = case retention)
                for ev in obs.evidence:
                    expires = now + timedelta(days=case.retention_days)
                    db_ev = DbEvidence(
                        id=ev.id,
                        case_id=case.id,
                        source_url=ev.source_url,
                        kind=ev.kind,
                        content_hash=ev.content_hash,
                        storage_key=ev.storage_key,
                        http_status=ev.http_status,
                        user_agent=ev.user_agent,
                        fetched_at=ev.fetched_at,
                        expires_at=expires,
                    )
                    db.add(db_ev)

                db_obs = DbObservation(
                    id=obs.id,
                    scan_id=scan_id,
                    task_id=db_task.id,
                    connector=obs.connector,
                    platform=obs.platform.value,
                    kind=obs.kind.value,
                    subject=obs.subject,
                    url=obs.url,
                    tier=obs.tier.value,
                    data=obs.data,
                    blocked=obs.blocked,
                    error=obs.error,
                )
                db.add(db_obs)
                stats.total_observations += 1

                # Normalize into entities + edges (sirali merge -> deterministik)
                ents, edges = connector.normalize(obs)
                for ent in ents:
                    existing = entity_map.get(ent.id)
                    if existing is None:
                        entity_map[ent.id] = ent
                    else:
                        _merge_entity(existing, ent)
                    # Entity'ler step 2b'de toplu yazilir (dedupe + merge sonrasi)

                for edge in edges:
                    edge_map[edge.id] = edge

                await event_broadcaster.broadcast(
                    scan_id,
                    "observation.created",
                    {
                        "observation_id": obs.id,
                        "kind": obs.kind.value,
                        "platform": obs.platform.value,
                    },
                )

            await event_broadcaster.broadcast(
                scan_id, "task.finished", {"task_id": db_task.id, "observations": len(obs_list)}
            )
            await db.commit()

            # Artimli skorlama: bulgular tarama bitmeden Bulgular sekmesine duser
            if stats.executed % 10 == 0:
                await self._write_findings(
                    db,
                    scan,
                    case,
                    target,
                    list(entity_map.values()),
                    seed_emails,
                    notify=False,
                    explain=False,
                )

        def _completion_key(finished: asyncio.Task[_TaskOutcome]) -> tuple[float, str]:
            db_task, _spec, _conn = inflight[finished]
            created = db_task.created_at
            # Farkli oturumlardan gelen satirlarda sqlite naive, Python tarafi
            # aware olabilir: karsilastirmayi sayisal anahtara indirgeyerek
            # "naive vs aware" tip hatasini onle.
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (created.timestamp() if created is not None else 0.0, db_task.id)

        # SEC-09: butce HTTP katmaninda fail-closed uygulanir — limit asilinca
        # istek atilmaz (`BudgetExceededError`). Bu baglam (ve alt gorevleri)
        # ayni `RequestBudget` nesnesini paylasir, sayim paralelde dogrudur.
        budget_token = set_request_budget(budget.max_http_requests)
        # Iptal sinyali, uzun sureli bir gorevin bitmesini beklemeden donguyu
        # uyandirsin: `asyncio.wait` hem gorevleri hem event'i dinler.
        cancel_event = self.cancel_event(scan_id)
        cancel_waiter = asyncio.create_task(cancel_event.wait())

        try:
            while True:
                if not stats.cancelled and await _is_cancelled():
                    # Iptal: yeni gorev dispatch edilmez, ucustakiler kesilir.
                    # Durum DB'ye de yazilir (kanal tek basina da tutarli kalsin).
                    stats.cancelled = True
                    await db.execute(
                        update(Scan)
                        .where(Scan.id == scan_id, Scan.status == "running")
                        .values(status="cancelled")
                        .execution_options(synchronize_session=False)
                    )
                    await db.commit()
                    for running in inflight:
                        running.cancel()

                if not pending:
                    await _refill_pending()
                if not pending and not inflight:
                    break

                if not stats.cancelled:
                    if pending and stats.budget_hit is None:
                        stats.budget_hit = _budget_reason()
                    if pending and stats.budget_hit is None:
                        stats.budget_hit = await _dispatch()

                if not inflight:
                    break

                waiters: set[asyncio.Task[Any]] = set(inflight)
                if not stats.cancelled and not cancel_waiter.done():
                    waiters.add(cancel_waiter)
                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                # Iptal sinyali bir gorev degil: sadece uyandirir
                done.discard(cancel_waiter)
                if not done:
                    continue
                # Ayni turda biten gorevler planlama sirasina gore yazilir:
                # entity merge'i tekrar uretilebilir kalsin.
                for finished in sorted(done, key=_completion_key):
                    db_task, spec, connector = inflight.pop(finished)
                    if finished.cancelled():
                        # Iptal edilen gorev: gorev durum sozlugunu (done/failed/
                        # skipped) bozmamak icin `skipped` yazilir, nedeni ayri.
                        stats.cancelled_tasks += 1
                        db_task.status = "skipped"
                        db_task.error = db_task.error or "cancelled"
                        db_task.finished_at = datetime.now(timezone.utc)
                        await db.commit()
                        await event_broadcaster.broadcast(
                            scan_id, "task.failed", {"task_id": db_task.id, "error": "cancelled"}
                        )
                        continue
                    try:
                        await _persist(db_task, spec, connector, finished.result())
                    except Exception as exc:
                        # Tek gorevin yazim hatasi taramayi durdurmaz.
                        logger.exception("Gorev sonucu yazilamadi (%s)", db_task.id)
                        await db.rollback()
                        try:
                            db_task.status = "failed"
                            db_task.error = db_task.error or f"persist_error: {exc}"
                            db_task.finished_at = datetime.now(timezone.utc)
                            await db.commit()
                        except Exception:  # pragma: no cover - cift ariza
                            logger.exception("Gorev durumu yazilamadi (%s)", db_task.id)
        finally:
            reset_request_budget(budget_token)
            cancel_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_waiter
            # Tarama beklenmedik bir hatayla kesilirse ucan gorevleri birakma
            if inflight:
                for task in inflight:
                    task.cancel()
                await asyncio.gather(*inflight, return_exceptions=True)
                inflight.clear()

        return stats

    async def _write_findings(
        self,
        db: AsyncSession,
        scan: Scan,
        case: Case,
        target: Target,
        entities: list[Entity],
        seed_emails: set[str],
        *,
        notify: bool = True,
        explain: bool = True,
    ) -> None:
        """Entity'leri skorlar ve finding'leri yazar (artimli cagrilabilir)."""
        # Finding yazimi entity satirlarini gerektirir (FK) — artimli gecislerde
        # entity'ler henuz DB'ye yazilmamis olabilir.
        await self._persist_entities(db, case, entities)

        # Resolve findings — cross-platform sinyalleri hesapla
        explained_count = 0
        username_platforms: dict[str, set[str]] = {}
        for ent in entities:
            username_platforms.setdefault(_entity_username(ent), set()).add(ent.platform.value)

        avatar_platforms: dict[str, set[str]] = {}
        for ent in entities:
            avatar_hash = (ent.attributes or {}).get("avatar_sha256")
            if avatar_hash:
                avatar_platforms.setdefault(avatar_hash, set()).add(ent.platform.value)

        # Platform+identifier -> entity eslemesi (cross_link icin)
        entity_lookup: dict[tuple[str, str], Entity] = {}
        for ent in entities:
            entity_lookup[(ent.platform.value, _entity_username(ent))] = ent

        for ent in entities:
            uname = _entity_username(ent)
            username_count = len(username_platforms.get(uname, set()))
            avatar_hash = (ent.attributes or {}).get("avatar_sha256")
            avatar_count = len(avatar_platforms.get(avatar_hash, set())) if avatar_hash else 0

            # cross_link: bio linkleri baska platform entity'siyle eslesiyor mu
            has_cross = False
            for link in ent.attributes.get("links") or []:
                plat, subj = extract_platform_from_url(str(link))
                if plat != Platform.WEB and subj and (
                    plat.value,
                    subj.lower(),
                ) in entity_lookup:
                    has_cross = True
                    break
            if not has_cross:
                for key in ("github_link", "twitter_link", "telegram_link", "linkedin"):
                    link = ent.attributes.get(key)
                    if link:
                        plat, subj = extract_platform_from_url(str(link))
                        if plat != Platform.WEB and subj and (
                            plat.value,
                            subj.lower(),
                        ) in entity_lookup:
                            has_cross = True
                            break

            # commit e-postasi / pgp eslesmesi
            email_match = False
            ent_emails = {
                str(e).lower()
                for e in (ent.attributes.get("commit_emails") or [])
                if e
            }
            pub_email = ent.attributes.get("public_email") or ent.attributes.get(
                "email"
            )
            if pub_email:
                ent_emails.add(str(pub_email).lower())
            if ent_emails & seed_emails:
                email_match = True

            # avatar_match: ayni sha256 veya algisal dHash eslesmesi
            is_avatar_match = avatar_count >= 2
            if not is_avatar_match:
                ent_dhash = (ent.attributes or {}).get("avatar_dhash")
                if ent_dhash and ent_dhash not in ("0000000000000000", "ffffffffffffffff"):
                    for other in entities:
                        if other.id != ent.id and other.platform != ent.platform:
                            other_dhash = (other.attributes or {}).get("avatar_dhash")
                            if other_dhash and hamming_distance(ent_dhash, other_dhash) <= 6:
                                is_avatar_match = True
                                break

            authenticity = compute_authenticity(
                ent.attributes or {}, platforms_with_same_username=username_count
            )
            finding = self.resolver.resolve_entity(
                ent,
                target,
                case_id=case.id,
                scan_id=scan.id,
                username_platform_count=username_count,
                has_cross_link=has_cross,
                avatar_match=is_avatar_match,
                email_or_pgp_match=email_match,
                authenticity_score=authenticity.score,
            )

            # LLM/deterministik aciklama (skoru degistirmez).
            # LLM cagrisi ~6 sn surer: sadece aday+ bulgular ve son gecis aciklanir.
            if explain and finding.score >= 0.5 and explained_count < _MAX_LLM_EXPLANATIONS:
                explained_count += 1
                await self.explainer.explain(finding, target)
            else:
                await self.local_explainer.explain(finding, target)

            db_finding = DbFinding(
                id=finding.id,
                case_id=case.id,
                scan_id=scan.id,
                entity_id=ent.id,
                platform=finding.platform.value,
                url=finding.url,
                label=finding.label,
                status=finding.status.value,
                score=finding.score,
                breakdown=finding.breakdown.model_dump(),
                explanation=finding.explanation,
                evidence_ids=finding.evidence_ids,
            )
            # SEC-15: kullanicinin confirmed/rejected karari yeniden taramada
            # EZILMEZ. Karar varsa (verified_at dolu veya status candidate degil)
            # yalniz skor/breakdown/aciklama guncellenir.
            existing = await db.get(DbFinding, db_finding.id)
            if existing is None:
                db.add(db_finding)
            elif existing.verified_at is not None or existing.status in (
                "confirmed",
                "rejected",
            ):
                existing.score = db_finding.score
                existing.breakdown = db_finding.breakdown
                existing.explanation = db_finding.explanation
            else:
                existing.scan_id = db_finding.scan_id
                existing.entity_id = db_finding.entity_id
                existing.platform = db_finding.platform
                existing.url = db_finding.url
                existing.label = db_finding.label
                existing.status = db_finding.status
                existing.score = db_finding.score
                existing.breakdown = db_finding.breakdown
                existing.explanation = db_finding.explanation
                existing.evidence_ids = db_finding.evidence_ids
            if notify:
                await event_broadcaster.broadcast(
                    scan.id,
                    "finding.created",
                    {
                        "finding_id": finding.id,
                        "label": finding.label,
                        "score": finding.score,
                        "status": finding.status.value,
                    },
                )

        await db.commit()
        if not notify:
            await event_broadcaster.broadcast(
                scan.id, "findings.updated", {"count": len(entities)}
            )

    async def _persist_entities(self, db: AsyncSession, case: Case, entities: list[Entity]) -> None:
        """Entity satirlarini upsert eder (idempotent)."""
        for ent in entities:
            await db.merge(
                DbEntity(
                    id=ent.id,
                    case_id=case.id,
                    type=ent.type.value,
                    platform=ent.platform.value,
                    label=ent.label,
                    url=ent.url,
                    attributes=ent.attributes,
                )
            )
        await db.commit()

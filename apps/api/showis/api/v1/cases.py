import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import showis.db.session as db_session_module
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from showis.db.models import Case, Edge, Entity, Evidence, Finding, Report, Scan
from showis.db.models import Seed as DbSeed
from showis.db.models import Target as DbTarget
from showis.db.session import get_db
from showis.services.audit import list_audit_logs, record_request_audit
from showis.services.connectors_factory import build_connectors, build_llm_client
from showis.services.report_service import create_report
from showis.services.retention import _evidence_file_path
from showis.services.scanner import ScannerService
from showis_contracts import Platform, Target
from showis_engine.policy import guard_target
from showis_engine.scan_modes import budget_for_mode, estimate_scan, norm_mode
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger("showis.api.cases")

router = APIRouter(prefix="/cases", tags=["cases"])

#: Girdi doğrulama sınırları (SEC-14)
MAX_NAME = 256
MAX_PURPOSE = 4000
MAX_NOTES = 4000
MAX_HINT_VALUE = 200
MAX_HINTS = 10
MAX_UID_FIELD = 512
HINT_KEYS = {"school", "city", "company", "username", "email", "extra"}
USERNAME_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")

#: `budget` anahtarları — mod profilinin üstüne çıkılamaz (SEC-05)
_BUDGET_KEYS = ("max_tasks", "max_http_requests", "max_llm_calls", "max_wall_seconds")


def _clean_hints(hints: dict[str, Any]) -> dict[str, Any]:
    if not hints:
        return {}
    if len(hints) > MAX_HINTS:
        raise ValueError(f"hints en fazla {MAX_HINTS} anahtar içerebilir")
    unknown = sorted(set(hints) - HINT_KEYS)
    if unknown:
        raise ValueError(f"izinli hint anahtarları: {sorted(HINT_KEYS)} (gelen: {unknown})")
    cleaned: dict[str, Any] = {}
    for key, value in hints.items():
        if isinstance(value, (dict, list, tuple, set)):
            raise ValueError(f"hints.{key} iç içe yapı olamaz")
        if not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"hints.{key} metin veya sayı olmalı")
        if isinstance(value, str):
            value = " ".join(value.split())
            if len(value) > MAX_HINT_VALUE:
                raise ValueError(f"hints.{key} en fazla {MAX_HINT_VALUE} karakter")
        cleaned[key] = value
    return cleaned


class SeedInput(BaseModel):
    platform: Platform
    url: str | None = Field(default=None, max_length=MAX_UID_FIELD)
    username: str | None = Field(default=None, pattern=USERNAME_PATTERN)
    email: str | None = Field(default=None, max_length=254)

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            return None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("url http(s) ile başlamalı")
        return value.strip()

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        candidate = value.strip()
        if not _EMAIL_RE.match(candidate):
            raise ValueError("email biçimi geçersiz")
        return candidate


class TargetInput(BaseModel):
    first_name: str = Field(min_length=2, max_length=80)
    last_name: str = Field(min_length=2, max_length=80)
    hints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("first_name", "last_name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        cleaned = " ".join(str(value).split())
        if len(cleaned) < 2:
            raise ValueError("ad/soyad en az 2 karakter olmalı")
        return cleaned

    @field_validator("hints")
    @classmethod
    def _check_hints(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _clean_hints(value)


class CaseCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=MAX_NAME)
    purpose: str = Field(min_length=10, max_length=MAX_PURPOSE)
    #: consent | legitimate_interest | self_check
    legal_basis: Literal["consent", "legitimate_interest", "self_check"]
    notes: str | None = Field(default=None, max_length=MAX_NOTES)
    retention_days: int = Field(default=30, ge=1, le=365)
    target: TargetInput
    seeds: list[SeedInput] = Field(default_factory=list, max_length=20)


class ScanStartRequest(BaseModel):
    mode: Literal["O1", "O2", "O3", "o1", "o2", "o3"] = "O2"
    #: Geriye dönük uyumlu alan: değerler mod profilinin üstüne çıkamaz,
    #: bilinmeyen anahtarlar yok sayılır (SEC-05).
    budget: dict[str, Any] | None = None



@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(
    data: CaseCreateRequest, request: Request, db: AsyncSession = Depends(get_db)
):
    # SEC-07: reşit-olmayan / uygun olmayan hedef sinyali burada durdurulur.
    contract_target = Target(
        first_name=data.target.first_name,
        last_name=data.target.last_name,
        hints=data.target.hints,
    )
    guard_target(contract_target)
    if contract_target.review_required:
        await record_request_audit(
            db=db,
            request=request,
            action="target.blocked",
            case_id=None,
            target="Target: redacted",
            details={
                "reason": "review_required",
                "hints_keys": sorted(data.target.hints),
                "stage": "case.create",
            },
        )
        raise HTTPException(
            status_code=422,
            detail=(
                "Hedef inceleme gerektiriyor: reşit olmayan kişi sinyali tespit edildi "
                "(AUP md.7). Bu hedef için tarama başlatılamaz."
            ),
        )

    case = Case(
        name=data.name,
        purpose=data.purpose,
        legal_basis=data.legal_basis,
        notes=data.notes,
        retention_days=data.retention_days,
    )
    db.add(case)
    await db.flush()

    target = DbTarget(
        case_id=case.id,
        first_name=data.target.first_name,
        last_name=data.target.last_name,
        hints=data.target.hints,
        review_required=False,
    )
    db.add(target)

    for s in data.seeds:
        seed = DbSeed(
            case_id=case.id,
            platform=s.platform,
            url=s.url,
            username=s.username,
            email=s.email,
        )
        db.add(seed)

    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="case.create",
        case_id=case.id,
        target=f"Case: {case.name}",
        details={"legal_basis": case.legal_basis, "purpose": case.purpose},
    )

    return {
        "id": case.id,
        "name": case.name,
        "status": case.status,
        "created_at": case.created_at.isoformat(),
    }


@router.get("")
async def list_cases(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Case).order_by(Case.created_at.desc()))
    cases = res.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "purpose": c.purpose,
            "legal_basis": c.legal_basis,
            "status": c.status,
            "retention_days": c.retention_days,
            "created_at": c.created_at.isoformat(),
        }
        for c in cases
    ]


@router.get("/{case_id}")
async def get_case(case_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Case)
        .options(selectinload(Case.targets), selectinload(Case.seeds))
        .where(Case.id == case_id)
    )
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    target = case.targets[0] if case.targets else None
    return {
        "id": case.id,
        "name": case.name,
        "purpose": case.purpose,
        "legal_basis": case.legal_basis,
        "notes": case.notes,
        "status": case.status,
        "retention_days": case.retention_days,
        "created_at": case.created_at.isoformat(),
        "target": {
            "first_name": target.first_name,
            "last_name": target.last_name,
            "hints": target.hints,
        }
        if target
        else None,
        "seeds": [
            {
                "id": s.id,
                "platform": s.platform,
                "url": s.url,
                "username": s.username,
                "email": s.email,
            }
            for s in case.seeds
        ],
    }


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Case).where(Case.id == case_id))
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    await record_request_audit(
        db=db,
        request=request,
        action="case.delete",
        case_id=case.id,
        target=f"Case: {case.name}",
    )
    # Kanit dosyalari DB cascade'i ile silinmez: satirlardan once diskteki
    # dosyalari kaldir (KVKK md.7 — silme talebi aninda karsilanir).
    storage_keys = (
        await db.execute(select(Evidence.storage_key).where(Evidence.case_id == case_id))
    ).scalars()
    for storage_key in storage_keys:
        path = _evidence_file_path(storage_key)
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                logger.warning("Kanit dosyasi silinemedi: %s", path)

    # T-112/4 (SEC-12): rapor dosyalari DB cascade'i ile silinmez; diskte
    # kalan PDF/JSON (tam PII) kalirsa KVKK md.7 fiilen ihlal edilir.
    failed_paths, removed = await _cleanup_report_files(db, case_id)
    if failed_paths:
        await record_request_audit(
            db=db,
            request=request,
            action="report.cleanup_failed",
            case_id=case.id,
            target=f"Case: {case.name}",
            details={"failed_count": len(failed_paths), "removed_count": removed},
        )
    logger.info(
        "Case silindi: %s (rapor dosyasi silinen=%d, basarisiz=%d)",
        case_id,
        removed,
        len(failed_paths),
    )

    # Toplu silme: ORM iliskileri async'te lazy-load edemez; DB seviyesi
    # ondelete=CASCADE tum cocuk kayitlari temizler. AuditLog'da case_id
    # FK'siz oldugundan silme kaydi korunur.
    await db.execute(delete(Case).where(Case.id == case_id))
    await db.commit()
    return None


def _report_dir() -> Path:
    """Rapor dizini (import aninda degil, cagri aninda env'den okunur)."""
    return Path(os.getenv("SHOWIS_REPORT_DIR", "./data/reports"))


async def _cleanup_report_files(
    db: AsyncSession, case_id: str
) -> tuple[list[str], int]:
    """Vakaya ait rapor dosyalarini siler.

    Iki kaynak birlikte kullanilir: `reports.storage_key` (gercek yol) ve
    `SHOWIS_REPORT_DIR` altinda adinda case_id gecen dosyalar (yetim kalanlar).

    Returns:
        (silinemeyen_yollar, silinen_sayi)
    """
    candidates: set[Path] = set()
    stored = (
        await db.execute(select(Report.storage_key).where(Report.case_id == case_id))
    ).scalars()
    for storage_key in stored:
        if storage_key:
            candidates.add(Path(str(storage_key)))

    report_dir = _report_dir()
    if report_dir.is_dir():
        try:
            candidates.update(report_dir.glob(f"*{case_id}*"))
        except OSError as exc:  # izin vb.
            logger.warning("Rapor dizini taranamadi (%s): %s", report_dir, type(exc).__name__)

    failed: list[str] = []
    removed = 0
    for path in sorted(candidates):
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except OSError as exc:
            # Sessiz yutma yok: log + audit (cagiran taraf kaydi yazar)
            logger.warning(
                "Rapor dosyasi silinemedi (%s): %s", path, type(exc).__name__
            )
            failed.append(str(path))
    return failed, removed


def _use_celery() -> bool:
    return os.getenv("USE_CELERY", "0") == "1"


def _dispatch_celery_task(scan: Scan) -> bool:
    """``showis.scan.run`` görevini kuyruğa atar ve id'yi ``scan.stats``e yazar.

    T-112/1: scan satırı **önce commit edilir** (worker satırı görebilsin), sonra
    görev kuyruğa atılır ve dönen id `scan.stats["celery_task_id"]` olarak hemen
    kalıcılaştırılır. `POST /scans/{id}/cancel` bu alanı okuyup
    `celery_app.control.revoke(task_id, terminate=True)` çağırır.
    Broker erişilemezse sessiz yutma yok: uyarı loglanır ve `False` döner (çağıran
    süreç içi yolu seçer).
    """
    from showis.services.worker import celery_app

    try:
        result = celery_app.send_task("showis.scan.run", args=[scan.id], queue="osint")
    except Exception as exc:  # noqa: BLE001 - broker hatasi tarama baslatmayi bloklamasin
        logger.warning(
            "Celery dispatch basarisiz (scan=%s): %s", scan.id, type(exc).__name__
        )
        return False

    task_id = getattr(result, "id", None)
    if not task_id:
        logger.warning("Celery send_task id dondurmedi (scan=%s)", scan.id)
        return False

    stats = dict(scan.stats or {})
    stats["celery_task_id"] = str(task_id)
    scan.stats = stats
    return True


async def _run_scan_inline(scan_id: str) -> None:
    """Süreç içi tarama (USE_CELERY=0 veya broker erişilemezse)."""
    async with db_session_module.async_session_factory() as session:
        connectors = await build_connectors(session)
        llm_client = await build_llm_client(session)
        scanner = ScannerService(connectors=connectors, llm_client=llm_client)
        await scanner.execute_scan(scan_id, session)


async def run_scan_in_background(scan_id: str):
    """Geriye dönük giriş noktası (testler ve eski çağrılar bunu yamalar)."""
    if _use_celery():
        from showis.services.worker import celery_app

        celery_app.send_task("showis.scan.run", args=[scan_id], queue="osint")
        return
    await _run_scan_inline(scan_id)


@router.get("/{case_id}/scan/estimate")
async def estimate_case_scan(
    case_id: str, mode: str = "O2", db: AsyncSession = Depends(get_db)
):
    """Tarama baslamadan once tahmini sure/gorev sayisini doner."""
    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Case target not found")
    contract_target = Target(
        first_name=target.first_name,
        last_name=target.last_name,
        hints=target.hints or {},
    )
    return estimate_scan(contract_target, mode)


def _clamp_budget(requested: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    """İstemci bütçesini mod profilinin üstüne çıkaramaz (SEC-05).

    Yalnız bilinen anahtarlar dikkate alınır; her değer ``min()`` ile profile
    kırpılır. Sonuç her zaman güvenli mod profilidir.
    """
    profile = budget_for_mode(mode)
    if not requested:
        return profile
    clamped = dict(profile)
    for key in _BUDGET_KEYS:
        if key not in requested:
            continue
        raw = requested[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        value = int(raw)
        if value <= 0:
            continue
        clamped[key] = min(value, int(profile[key]))
    return clamped


async def _find_active_scan(db: AsyncSession, case_id: str) -> Scan | None:
    """Vakadaki `queued`/`running` taramayi doner (SEC-05 on kontrolu).

    DB seviyesindeki garanti `uq_scans_case_active` kismi unique index'idir;
    bu fonksiyon yalnizca hizli 409 uretir.
    """
    return (
        await db.execute(
            select(Scan)
            .where(Scan.case_id == case_id, Scan.status.in_(("queued", "running")))
            .order_by(Scan.created_at.desc())
        )
    ).scalars().first()


async def _load_contract_target(db: AsyncSession, case_id: str) -> Target | None:
    db_target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    if not db_target:
        return None
    return Target(
        first_name=db_target.first_name,
        last_name=db_target.last_name,
        hints=db_target.hints or {},
        review_required=bool(db_target.review_required),
    )


@router.post("/{case_id}/scan")
async def start_scan(
    case_id: str,
    payload: ScanStartRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Case).where(Case.id == case_id))
    case = res.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # SEC-07 (defense in depth): planlama öncesi hedef yeniden denetlenir.
    contract_target = await _load_contract_target(db, case_id)
    if contract_target is None:
        raise HTTPException(status_code=404, detail="Case target not found")
    guard_target(contract_target)
    if contract_target.review_required or case.status == "review_pending":
        await record_request_audit(
            db=db,
            request=request,
            action="target.blocked",
            case_id=case_id,
            target="Target: redacted",
            details={"reason": "review_required", "stage": "scan.start"},
        )
        raise HTTPException(
            status_code=422,
            detail="Hedef inceleme gerektiriyor: tarama başlatılamaz (AUP md.7).",
        )

    # SEC-05: aynı vakada tek aktif tarama.
    active = await _find_active_scan(db, case_id)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "scan_already_active",
                "scan_id": active.id,
                "status": active.status,
            },
        )

    mode = norm_mode(payload.mode)
    scan = Scan(
        case_id=case.id,
        status="queued",
        mode=mode,
        budget=_clamp_budget(payload.budget, mode),
        policy_snapshot={"tier": "B", "safe_mode": True},
        stats={},
    )
    db.add(scan)
    dispatch_failed = False
    try:
        # id'yi flush ile üret (PK sunucu tarafında da oluşsun) ve satırı commit et:
        # Celery worker'ı taramayı ancak satır görünür olduktan sonra koşmalı.
        await db.flush()
        await db.commit()
    except IntegrityError:
        # T-112/2: 0003_active_scan_unique kismi index'i — yaris kaybedildi.
        await db.rollback()
        active = await _find_active_scan(db, case_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "scan_already_active",
                "scan_id": active.id if active else None,
                "status": active.status if active else None,
            },
        ) from None
    await db.refresh(scan)

    if _use_celery():
        # T-112/1: commit sonrası dispatch; task id `scan.stats`a yazılıp
        # hemen kalıcılaştırılır — iptal ucu (scans.py) bu alanı okur.
        dispatch_failed = not _dispatch_celery_task(scan)
        if not dispatch_failed:
            try:
                await db.commit()
            except Exception:  # noqa: BLE001 - stats yazımı taramayı bloklamaz
                logger.warning("celery_task_id yazilamadi (scan=%s)", scan.id)
                await db.rollback()
    await db.refresh(scan)

    await record_request_audit(
        db=db,
        request=request,
        action="scan.start",
        case_id=case.id,
        target=f"Scan: {scan.id}",
        details={
            "mode": scan.mode,
            "budget": scan.budget,
            "budget_clamped": bool(payload.budget),
            "celery_task_id": (scan.stats or {}).get("celery_task_id"),
            "celery_dispatch_failed": dispatch_failed,
        },
    )

    if dispatch_failed:
        # Broker yok/erişilemez: tarama sessizce kuyrukta kalmasın, süreç içinde koş.
        background_tasks.add_task(_run_scan_inline, scan.id)
    elif not _use_celery():
        background_tasks.add_task(run_scan_in_background, scan.id)

    return {
        "scan_id": scan.id,
        "case_id": case.id,
        "status": scan.status,
        "mode": scan.mode,
    }


@router.get("/{case_id}/findings")
async def get_case_findings(case_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Finding).where(Finding.case_id == case_id).order_by(Finding.score.desc())
    )
    findings = res.scalars().all()
    return [
        {
            "id": f.id,
            "entity_id": f.entity_id,
            "platform": f.platform,
            "label": f.label,
            "url": f.url,
            "status": f.status,
            "score": f.score,
            "breakdown": f.breakdown,
            "explanation": f.explanation,
            "evidence_ids": f.evidence_ids,
            "verified_at": f.verified_at.isoformat() if f.verified_at else None,
        }
        for f in findings
    ]


@router.get("/{case_id}/export")
async def export_case(
    case_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """KVKK veri sahibi talebi: vakanin tum verisini JSON olarak disar aktarir."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    target = (
        await db.execute(select(DbTarget).where(DbTarget.case_id == case_id))
    ).scalar_one_or_none()
    seeds = (await db.execute(select(DbSeed).where(DbSeed.case_id == case_id))).scalars().all()
    entities = (await db.execute(select(Entity).where(Entity.case_id == case_id))).scalars().all()
    edges = (await db.execute(select(Edge).where(Edge.case_id == case_id))).scalars().all()
    findings = (await db.execute(select(Finding).where(Finding.case_id == case_id))).scalars().all()
    evidence = (
        (await db.execute(select(Evidence).where(Evidence.case_id == case_id))).scalars().all()
    )

    await record_request_audit(
        db=db,
        request=request,
        action="case.export",
        case_id=case_id,
        target=f"Case: {case.name}",
    )

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "case": {
            "id": case.id,
            "name": case.name,
            "purpose": case.purpose,
            "legal_basis": case.legal_basis,
            "notes": case.notes,
            "status": case.status,
            "retention_days": case.retention_days,
            "created_at": case.created_at.isoformat(),
        },
        "target": {
            "first_name": target.first_name,
            "last_name": target.last_name,
            "name_forms": target.name_forms,
            "hints": target.hints,
            "review_required": target.review_required,
        }
        if target
        else None,
        "seeds": [
            {
                "platform": s.platform,
                "url": s.url,
                "username": s.username,
                "email": s.email,
                "source": s.source,
            }
            for s in seeds
        ],
        "entities": [
            {
                "id": e.id,
                "type": e.type,
                "platform": e.platform,
                "label": e.label,
                "url": e.url,
                "attributes": e.attributes,
                "score": e.score,
            }
            for e in entities
        ],
        "edges": [
            {
                "id": ed.id,
                "type": ed.type,
                "source": ed.source_entity_id,
                "target": ed.target_entity_id,
                "confidence": ed.confidence,
                "reason": ed.reason,
            }
            for ed in edges
        ],
        "findings": [
            {
                "id": f.id,
                "entity_id": f.entity_id,
                "platform": f.platform,
                "label": f.label,
                "url": f.url,
                "status": f.status,
                "score": f.score,
                "breakdown": f.breakdown,
                "explanation": f.explanation,
                "evidence_ids": f.evidence_ids,
            }
            for f in findings
        ],
        "evidence": [
            {
                "id": ev.id,
                "source_url": ev.source_url,
                "kind": ev.kind,
                "content_hash": ev.content_hash,
                "http_status": ev.http_status,
                "fetched_at": ev.fetched_at.isoformat() if ev.fetched_at else None,
            }
            for ev in evidence
        ],
    }


@router.get("/{case_id}/graph")
async def get_case_graph(case_id: str, db: AsyncSession = Depends(get_db)):
    """Cytoscape icin {nodes, edges} dondurur."""
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    entities = (await db.execute(select(Entity).where(Entity.case_id == case_id))).scalars().all()
    edges = (await db.execute(select(Edge).where(Edge.case_id == case_id))).scalars().all()
    findings = (await db.execute(select(Finding).where(Finding.case_id == case_id))).scalars().all()
    score_by_entity = {f.entity_id: f.score for f in findings}

    nodes = [
        {
            "data": {
                "id": e.id,
                "label": e.label,
                "type": e.type,
                "platform": e.platform,
                "url": e.url,
                "score": score_by_entity.get(e.id, e.score or 0.0),
                "attributes": e.attributes or {},
            }
        }
        for e in entities
    ]
    graph_edges = [
        {
            "data": {
                "id": ed.id,
                "source": ed.source_entity_id,
                "target": ed.target_entity_id,
                "label": ed.type,
                "type": ed.type,
                "confidence": ed.confidence,
                "reason": ed.reason,
            }
        }
        for ed in edges
    ]
    return {"nodes": nodes, "edges": graph_edges}


@router.get("/{case_id}/audit")
async def get_case_audit(case_id: str, db: AsyncSession = Depends(get_db)):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    logs = await list_audit_logs(db, case_id=case_id, limit=500)
    return [
        {
            "id": log.id,
            "actor": log.actor,
            "action": log.action,
            "target": log.target,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]


class ReportCreateRequest(BaseModel):
    format: str = "pdf"  # pdf | json


@router.post("/{case_id}/report")
async def create_case_report(
    case_id: str,
    payload: ReportCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        report, _content = await create_report(db, case_id, payload.format)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await record_request_audit(
        db=db,
        request=request,
        action="report.create",
        case_id=case_id,
        target=f"Report: {report.id} ({report.format})",
    )
    return {
        "id": report.id,
        "case_id": report.case_id,
        "format": report.format,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "download_url": f"/api/v1/reports/{report.id}/download",
    }

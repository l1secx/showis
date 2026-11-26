"""Havuz muhakemesi API'si: ilişkiler, pivotlar, korelasyon, hipotezler.

Değişmez: AI/kural/pivot çıktıları `proposed|candidate` olarak yazılır;
`confirmed` yalnız bu uçlara kullanıcı PATCH'iyle gelir.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from showis.db.models import Hypothesis, PoolItem, PoolRelation
from showis.db.session import get_db
from showis.services.audit import record_request_audit_safe
from showis.services.chain import DEFAULT_MAX_FETCHES, run_case_chain
from showis.services.connectors_factory import build_llm_client
from showis.services.reasoning.avatars import hash_pool_avatars
from showis.services.reasoning.correlate import (
    build_timeline,
    find_contradictions,
    run_correlation,
)
from showis.services.reasoning.graph import (
    RELATION_STATUSES,
    RELATION_TYPES,
    RelationError,
    graph_summary,
    identity_clusters,
    relation_to_dict,
    upsert_relation,
)
from showis.services.reasoning.hypotheses import (
    HYPOTHESIS_STATUSES,
    generate_hypotheses,
    hypothesis_to_dict,
)
from showis.services.reasoning.pivots import PivotError, pivots_for, run_pivot
from showis.services.reasoning.scoring import score
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["reasoning"])


class RelationCreate(BaseModel):
    src_item_id: str = Field(min_length=1, max_length=36)
    dst_item_id: str = Field(min_length=1, max_length=36)
    type: str = Field(min_length=1, max_length=32)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    note: str = Field(default="", max_length=300)
    status: str = Field(default="confirmed", max_length=16)


class RelationPatch(BaseModel):
    status: str | None = Field(default=None, max_length=16)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class HypothesisCreate(BaseModel):
    statement: str = Field(min_length=5, max_length=2000)
    relation_ids: list[str] = Field(default_factory=list, max_length=40)
    item_ids: list[str] = Field(default_factory=list, max_length=60)
    next_checks: list[str] = Field(default_factory=list, max_length=6)


class ChainRequest(BaseModel):
    #: Boşsa vakanın tüm çapaları (hedef, seed'ler, onaylı kayıtlar) kullanılır.
    item_ids: list[str] = Field(default_factory=list, max_length=40)
    max_fetches: int = Field(default=DEFAULT_MAX_FETCHES, ge=1, le=80)
    max_depth: int = Field(default=3, ge=0, le=5)


class HypothesisPatch(BaseModel):
    status: str | None = Field(default=None, max_length=16)
    statement: str | None = Field(default=None, min_length=5, max_length=2000)
    next_checks: list[str] | None = Field(default=None, max_length=6)


from .common import require_case as _require_case  # noqa: E402


async def _require_item(db: AsyncSession, case_id: str, item_id: str) -> PoolItem:
    item = (
        await db.execute(
            select(PoolItem).where(PoolItem.id == item_id, PoolItem.case_id == case_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Havuz kaydı bulunamadı")
    return item


async def _load(db: AsyncSession, case_id: str) -> tuple[list[PoolItem], list[PoolRelation]]:
    items = list(
        (await db.execute(select(PoolItem).where(PoolItem.case_id == case_id))).scalars().all()
    )
    relations = list(
        (await db.execute(select(PoolRelation).where(PoolRelation.case_id == case_id)))
        .scalars()
        .all()
    )
    return items, relations


def _actor(request: Request) -> str:
    actor = getattr(request.state, "actor", None)
    return str(getattr(actor, "username", None) or "operator")


# --- İlişkiler -----------------------------------------------------------------
@router.get("/{case_id}/reasoning/relations")
async def list_relations(
    case_id: str,
    status_filter: str | None = None,
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    query = select(PoolRelation).where(PoolRelation.case_id == case_id)
    if status_filter:
        query = query.where(PoolRelation.status == status_filter)
    if type:
        query = query.where(PoolRelation.type == type)
    rows = (await db.execute(query.order_by(PoolRelation.created_at.desc()).limit(500))).scalars()
    items = [relation_to_dict(row) for row in rows]
    return {"items": items, "total": len(items), "types": sorted(RELATION_TYPES)}


@router.post("/{case_id}/reasoning/relations", status_code=201)
async def create_relation(
    case_id: str,
    data: RelationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    try:
        row, created = await upsert_relation(
            db,
            case_id=case_id,
            src_item_id=data.src_item_id,
            dst_item_id=data.dst_item_id,
            type=data.type,
            confidence=data.confidence,
            evidence=[
                {"rule": "user", "source": _actor(request), "weight": 3.0, "note": data.note}
            ],
            status=data.status,
            source="user",
            created_by=_actor(request),
        )
    except RelationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not created and data.status in RELATION_STATUSES:
        row.status = data.status  # kullanıcı kararı mevcut ilişkiyi günceller
    await db.commit()
    await db.refresh(row)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.relation.create",
        case_id=case_id,
        target=f"PoolRelation: {row.id}",
        details={"type": row.type, "status": row.status},
    )
    return relation_to_dict(row)


@router.patch("/{case_id}/reasoning/relations/{relation_id}")
async def patch_relation(
    case_id: str,
    relation_id: str,
    data: RelationPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(PoolRelation).where(
                PoolRelation.id == relation_id, PoolRelation.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="İlişki bulunamadı")
    if data.status is not None:
        if data.status not in RELATION_STATUSES:
            raise HTTPException(status_code=422, detail="geçersiz durum")
        row.status = data.status
    if data.confidence is not None:
        row.confidence = data.confidence
    await db.commit()
    await db.refresh(row)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.relation.update",
        case_id=case_id,
        target=f"PoolRelation: {row.id}",
        details={"status": row.status},
    )
    return relation_to_dict(row)


@router.delete("/{case_id}/reasoning/relations/{relation_id}", status_code=204)
async def delete_relation(
    case_id: str, relation_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    row = (
        await db.execute(
            select(PoolRelation).where(
                PoolRelation.id == relation_id, PoolRelation.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="İlişki bulunamadı")
    await db.delete(row)
    await db.commit()
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.relation.delete",
        case_id=case_id,
        target=f"PoolRelation: {relation_id}",
        details={},
    )
    return None


# --- Graf, korelasyon, tutarlılık ----------------------------------------------
@router.get("/{case_id}/reasoning/graph")
async def reasoning_graph(case_id: str, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    items, relations = await _load(db, case_id)
    clusters = identity_clusters(items, relations)
    return {
        "nodes": [
            {
                "id": i.id,
                "kind": i.kind,
                "label": i.label,
                "value": i.value[:200],
                "status": i.status,
            }
            for i in items
            if i.status != "rejected"
        ],
        "edges": [relation_to_dict(r) for r in relations if r.status != "rejected"],
        "clusters": [
            {**c.as_dict(), "item_ids": list(c.entity_ids)} for c in clusters if c.size > 1
        ],
        "summary": graph_summary(items, relations),
    }


@router.post("/{case_id}/reasoning/correlate")
async def correlate(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    result = await run_correlation(db, case_id)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.correlate",
        case_id=case_id,
        target=f"Case: {case_id}",
        details={k: v for k, v in result.items() if k != "contradictions"}
        | {"contradictions": len(result["contradictions"])},
    )
    return result


@router.post("/{case_id}/reasoning/avatars/hash")
async def hash_avatars(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Havuz öğelerinin `avatar_url` görsellerini özetler (dHash korelasyonu için)."""
    await _require_case(db, case_id)
    result = await hash_pool_avatars(db, case_id)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.avatars",
        case_id=case_id,
        target=f"Case: {case_id}",
        details=result,
    )
    return result


@router.get("/{case_id}/reasoning/contradictions")
async def contradictions(case_id: str, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    items, relations = await _load(db, case_id)
    found = [c.to_dict() for c in find_contradictions(items, relations)]
    return {"items": found, "total": len(found)}


@router.get("/{case_id}/reasoning/timeline")
async def timeline(case_id: str, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    items, _ = await _load(db, case_id)
    rows = build_timeline(items)
    return {"items": rows, "total": len(rows)}


# --- Pivotlar -------------------------------------------------------------------
@router.get("/{case_id}/reasoning/items/{item_id}/pivots")
async def list_pivots(case_id: str, item_id: str, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    item = await _require_item(db, case_id, item_id)
    return {"items": [p.to_dict() for p in pivots_for(item.kind)]}


@router.post("/{case_id}/reasoning/items/{item_id}/pivots/{pivot_id}")
async def execute_pivot(
    case_id: str,
    item_id: str,
    pivot_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = await _require_case(db, case_id)
    item = await _require_item(db, case_id, item_id)
    try:
        result = await run_pivot(
            db, case=case, item=item, pivot_id=pivot_id, actor=f"pivot:{_actor(request)}"
        )
    except PivotError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.pivot",
        case_id=case_id,
        target=f"PoolItem: {item.id}",
        details={"pivot": pivot_id, "items": len(result.items), "relations": len(result.relations)},
    )
    return result.to_dict()


# --- Kimlik zinciri -------------------------------------------------------------
@router.post("/{case_id}/reasoning/chain")
async def run_chain(
    case_id: str,
    request: Request,
    body: ChainRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Onaylı bulgulardan sahip beyanlı bağlantı zincirini kurar (öneri yazar)."""
    case = await _require_case(db, case_id)
    params = body or ChainRequest()
    items = None
    if params.item_ids:
        items = [await _require_item(db, case_id, item_id) for item_id in params.item_ids]
        if any(item.status == "rejected" for item in items):
            raise HTTPException(status_code=422, detail="reddedilmiş kayıttan zincir kurulamaz")
    result = await run_case_chain(
        db,
        case=case,
        items=items,
        status="proposed",
        actor=f"chain:{_actor(request)}",
        max_fetches=params.max_fetches,
        max_depth=params.max_depth,
    )
    await db.commit()
    summary = result.summary()
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.chain",
        case_id=case_id,
        target=f"Case: {case_id}",
        details={k: v for k, v in summary.items() if k not in {"nodes", "notes"}},
    )
    return summary


# --- Hipotezler -----------------------------------------------------------------
async def _require_hypothesis(db: AsyncSession, case_id: str, hypothesis_id: str) -> Hypothesis:
    row = (
        await db.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id, Hypothesis.case_id == case_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Hipotez bulunamadı")
    return row


@router.get("/{case_id}/reasoning/hypotheses")
async def list_hypotheses(case_id: str, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    rows = (
        (
            await db.execute(
                select(Hypothesis)
                .where(Hypothesis.case_id == case_id)
                .order_by(Hypothesis.confidence.desc(), Hypothesis.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"items": [hypothesis_to_dict(r) for r in rows], "total": len(rows)}


@router.post("/{case_id}/reasoning/hypotheses/generate")
async def generate(case_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Kimlik kümelerinden hipotez üretir; LLM varsa yalnız gerekçeyi yazar."""
    await _require_case(db, case_id)
    client = None
    try:
        client = await build_llm_client(db, role="reasoner")
    except Exception:  # noqa: BLE001 - LLM yoksa deterministik çalış
        client = None
    rows = await generate_hypotheses(
        db, case_id=case_id, client=client, actor=f"ai:{_actor(request)}"
    )
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.hypotheses.generate",
        case_id=case_id,
        target=f"Case: {case_id}",
        details={"count": len(rows), "llm": client is not None},
    )
    return {"items": [hypothesis_to_dict(r) for r in rows], "total": len(rows)}


@router.post("/{case_id}/reasoning/hypotheses", status_code=201)
async def create_hypothesis(
    case_id: str,
    data: HypothesisCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    relations = []
    if data.relation_ids:
        relations = list(
            (
                await db.execute(
                    select(PoolRelation).where(
                        PoolRelation.case_id == case_id, PoolRelation.id.in_(data.relation_ids)
                    )
                )
            ).scalars()
        )
    supporting = [
        {**e, "relation_id": r.id}
        for r in relations
        if r.type != "contradicts"
        for e in (r.evidence or [])
    ]
    contradicting = [
        {**e, "relation_id": r.id}
        for r in relations
        if r.type == "contradicts"
        for e in (r.evidence or [])
    ]
    row = Hypothesis(
        case_id=case_id,
        statement=" ".join(data.statement.split()),
        confidence=score(supporting, contradicting),
        status="proposed",
        supporting=supporting[:40],
        contradicting=contradicting[:20],
        next_checks=[c[:300] for c in data.next_checks],
        item_ids=sorted(set(data.item_ids)),
        source="user",
        created_by=_actor(request),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.hypothesis.create",
        case_id=case_id,
        target=f"Hypothesis: {row.id}",
        details={},
    )
    return hypothesis_to_dict(row)


@router.patch("/{case_id}/reasoning/hypotheses/{hypothesis_id}")
async def patch_hypothesis(
    case_id: str,
    hypothesis_id: str,
    data: HypothesisPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    row = await _require_hypothesis(db, case_id, hypothesis_id)
    if data.status is not None:
        if data.status not in HYPOTHESIS_STATUSES:
            raise HTTPException(status_code=422, detail="geçersiz durum")
        row.status = data.status
    if data.statement is not None:
        row.statement = " ".join(data.statement.split())
    if data.next_checks is not None:
        row.next_checks = [c[:300] for c in data.next_checks]
    await db.commit()
    await db.refresh(row)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.hypothesis.update",
        case_id=case_id,
        target=f"Hypothesis: {row.id}",
        details={"status": row.status},
    )
    return hypothesis_to_dict(row)


@router.delete("/{case_id}/reasoning/hypotheses/{hypothesis_id}", status_code=204)
async def delete_hypothesis(
    case_id: str, hypothesis_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    row = await _require_hypothesis(db, case_id, hypothesis_id)
    await db.delete(row)
    await db.commit()
    await record_request_audit_safe(
        db=db,
        request=request,
        action="reasoning.hypothesis.delete",
        case_id=case_id,
        target=f"Hypothesis: {hypothesis_id}",
        details={},
    )
    return None

"""Vaka araştırma havuzu API'si."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from showis.core.security import get_actor
from showis.db.models import Case, PoolItem
from showis.db.session import get_db
from showis.services.audit import record_request_audit
from showis.services.breach import BreachError, check_email
from showis.services.pool import (
    MAX_BULK_ITEMS,
    POOL_KINDS,
    POOL_SOURCES,
    POOL_STATUSES,
    PoolValidationError,
    bulk_set_status,
    create_item,
    ensure_case_pool,
    mark_reasoning_stale,
    search_items,
    validate_kind,
)
from showis.services.reasoning.graph import delete_relations_for_item
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["pool"])


class PoolItemCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=256)
    value: str = Field(min_length=1, max_length=20000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class PoolItemPatch(BaseModel):
    label: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=16)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: dict[str, Any] | None = None


class PoolBulkStatus(BaseModel):
    """Toplu durum isteği — yalnız kullanıcı kararı (`confirmed|rejected`)."""

    item_ids: list[UUID] = Field(min_length=1, max_length=MAX_BULK_ITEMS)
    status: Literal["confirmed", "rejected"]


class PoolItemResponse(BaseModel):
    id: str
    case_id: str
    kind: str
    label: str
    value: str
    source: str
    status: str
    confidence: float
    attributes: dict[str, Any]
    provenance: dict[str, Any]
    created_by: str | None = None
    created_at: str
    updated_at: str


def _response(item: PoolItem) -> PoolItemResponse:
    return PoolItemResponse(
        id=item.id,
        case_id=item.case_id,
        kind=item.kind,
        label=item.label,
        value=item.value,
        source=item.source,
        status=item.status,
        confidence=item.confidence,
        attributes=item.attributes or {},
        provenance=item.provenance or {},
        created_by=item.created_by,
        created_at=item.created_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def _parse_csv(raw: str | None, allowed: frozenset[str], field: str) -> list[str]:
    if raw is None:
        return []
    values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    invalid = sorted({value for value in values if value not in allowed})
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Geçersiz {field}: {', '.join(invalid)} (izinli: {', '.join(sorted(allowed))})",
        )
    return list(dict.fromkeys(values))


async def _require_case(db: AsyncSession, case_id: str) -> Case:
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _require_item(db: AsyncSession, case_id: str, item_id: str) -> PoolItem:
    item = (
        await db.execute(
            select(PoolItem).where(PoolItem.id == item_id, PoolItem.case_id == case_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Pool item not found")
    return item


@router.get("/{case_id}/pool", response_model=dict[str, Any])
async def list_pool(
    case_id: str,
    q: str | None = Query(default=None, max_length=200),
    kind: str | None = Query(default=None),
    source: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    await ensure_case_pool(db, case_id)
    # `ensure_case_pool` commit eder; vaka bayat damgası için taze okunur.
    case = await _require_case(db, case_id)
    kinds = _parse_csv(kind, POOL_KINDS, "kind")
    sources = _parse_csv(source, POOL_SOURCES, "source")
    statuses = _parse_csv(status_filter, POOL_STATUSES, "status")
    items, total = await search_items(
        db,
        case_id=case_id,
        q=q,
        kinds=kinds,
        sources=sources,
        statuses=statuses,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_response(item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "reasoning_stale_at": (
            case.reasoning_stale_at.isoformat() if case.reasoning_stale_at else None
        ),
    }


@router.post("/{case_id}/pool", response_model=PoolItemResponse, status_code=201)
async def add_pool_item(
    case_id: str,
    data: PoolItemCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    try:
        kind = validate_kind(data.kind)
        item, created = await create_item(
            db,
            case_id=case_id,
            kind=kind,
            label=data.label,
            value=data.value,
            source="user",
            status="confirmed",
            confidence=data.confidence,
            attributes=data.attributes,
            provenance=data.provenance,
            created_by=request.state.actor.username
            if getattr(request.state, "actor", None)
            else "operator",
        )
    except PoolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(item)
    await record_request_audit(
        db=db,
        request=request,
        action="pool.item.create" if created else "pool.item.merge",
        case_id=case_id,
        target=f"PoolItem: {item.id}",
        details={"kind": item.kind, "source": item.source, "status": item.status},
    )
    return _response(item)


@router.post("/{case_id}/pool/bulk")
async def bulk_update_status(
    case_id: str,
    data: PoolBulkStatus,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Toplu onay/ret — yalnız kullanıcı kimlikli çağrı; AI yolu kullanmaz.

    Tek transaction: durum güncellemesi, bayat damgası ve audit birlikte yazılır.
    Bu vakanın dışındaki kimlikler güncellenmez; `skipped` olarak sayılır.
    """
    actor = get_actor(request)
    case = await _require_case(db, case_id)
    try:
        updated, skipped = await bulk_set_status(
            db,
            case_id=case_id,
            item_ids=[str(item_id) for item_id in data.item_ids],
            status=data.status,
        )
    except PoolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if updated:
        mark_reasoning_stale(case)
    # `record_request_audit` commit eder: güncelleme + bayat damgası + audit tek işlemdedir.
    await record_request_audit(
        db=db,
        request=request,
        action="pool.items.bulk_status",
        case_id=case_id,
        target=f"Case: {case_id}",
        details={
            "status": data.status,
            "updated": updated,
            "skipped": skipped,
            "actor": actor.username,
        },
    )
    return {"updated": updated, "skipped": skipped}


@router.post("/{case_id}/pool/{item_id}/breach-check")
async def breach_check(
    case_id: str,
    item_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """E-posta havuz kaydını sızıntı sağlayıcı zincirinde kontrol eder; sonucu kayda işler."""
    await _require_case(db, case_id)
    item = await _require_item(db, case_id, item_id)
    if item.kind != "email":
        raise HTTPException(
            status_code=422, detail="Leak kontrolü yalnız e-posta kayıtlarında yapılır"
        )
    try:
        result = await check_email(db, item.value)
    except BreachError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    attributes = dict(item.attributes or {})
    attributes["breach_check"] = result
    item.attributes = attributes
    await db.commit()
    await db.refresh(item)
    await record_request_audit(
        db=db,
        request=request,
        action="pool.breach_check",
        case_id=case_id,
        target=f"PoolItem: {item.id}",
        details={
            "breached": result.get("breached"),
            "breach_count": result.get("count"),
            "source": result.get("source"),
        },
    )
    return {"item_id": item.id, "breach": result}


@router.patch("/{case_id}/pool/{item_id}", response_model=PoolItemResponse)
async def update_pool_item(
    case_id: str,
    item_id: str,
    data: PoolItemPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    case = await _require_case(db, case_id)
    item = await _require_item(db, case_id, item_id)
    changed: list[str] = []
    stale = False
    if data.label is not None:
        label = " ".join(data.label.split())[:256]
        if not label:
            raise HTTPException(status_code=422, detail="label boş olamaz")
        if label != item.label:
            item.label = label
            changed.append("label")
            stale = True
    if data.status is not None:
        next_status = data.status.strip().lower()
        if next_status not in POOL_STATUSES:
            raise HTTPException(status_code=422, detail="geçersiz durum")
        if next_status != item.status:
            item.status = next_status
            changed.append("status")
            stale = True
    if data.confidence is not None:
        if data.confidence != item.confidence:
            item.confidence = data.confidence
            changed.append("confidence")
            stale = True
    if data.attributes is not None:
        merged = dict(item.attributes or {})
        merged.update(data.attributes)
        if merged != (item.attributes or {}):
            item.attributes = merged
            changed.append("attributes")
            stale = True
    # Kullanıcı kararı/düzenlemesi muhakeme çıktılarını bayatlatır.
    if stale:
        mark_reasoning_stale(case)
    await db.commit()
    await db.refresh(item)
    await record_request_audit(
        db=db,
        request=request,
        action="pool.item.update",
        case_id=case_id,
        target=f"PoolItem: {item.id}",
        details={"changed_fields": changed, "status": item.status},
    )
    return _response(item)


@router.delete("/{case_id}/pool/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool_item(
    case_id: str,
    item_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    case = await _require_case(db, case_id)
    item = await _require_item(db, case_id, item_id)
    kind = item.kind
    await delete_relations_for_item(db, item.id)
    await db.delete(item)
    # Havuzdan öğe kalkınca mevcut ilişki/hipotez çıktıları bayatlar.
    mark_reasoning_stale(case)
    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="pool.item.delete",
        case_id=case_id,
        target=f"PoolItem: {item_id}",
        details={"kind": kind},
    )
    return None

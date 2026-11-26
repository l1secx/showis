"""Tedarik zinciri API'si — düğüm, ilişki ve grafik."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from showis.db.models import SupplyChainEdge, SupplyChainNode
from showis.db.session import get_db
from showis.services.audit import record_request_audit_safe
from showis.services.pool import POOL_STATUSES, PoolValidationError, normalize_value
from showis.services.supply_chain import (
    NODE_KINDS,
    RELATIONS,
    create_edge,
    create_node,
    edge_to_dict,
    graph_data,
    list_edges,
    list_nodes,
    node_to_dict,
    validate_node_kind,
    validate_relation,
)
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["supply-chain"])


class NodeCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=2, max_length=256)
    country: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=4000)
    identifiers: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class NodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=256)
    country: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=4000)
    status_value: str | None = Field(default=None, alias="status", max_length=16)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    attributes: dict[str, Any] | None = None
    identifiers: dict[str, Any] | None = None


class EdgeCreate(BaseModel):
    source_id: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=64)
    relation: str = Field(min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=256)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)


class EdgePatch(BaseModel):
    label: str | None = Field(default=None, max_length=256)
    status_value: str | None = Field(default=None, alias="status", max_length=16)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: dict[str, Any] | None = None


from .common import require_case as _require_case  # noqa: E402


async def _require_node(db: AsyncSession, case_id: str, node_id: str) -> SupplyChainNode:
    node = (
        await db.execute(
            select(SupplyChainNode).where(
                SupplyChainNode.id == node_id, SupplyChainNode.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Düğüm bulunamadı")
    return node


async def _require_edge(db: AsyncSession, case_id: str, edge_id: str) -> SupplyChainEdge:
    edge = (
        await db.execute(
            select(SupplyChainEdge).where(
                SupplyChainEdge.id == edge_id, SupplyChainEdge.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if edge is None:
        raise HTTPException(status_code=404, detail="İlişki bulunamadı")
    return edge


def _parse_csv(raw: str | None, allowed: frozenset[str], field: str) -> list[str]:
    if raw is None:
        return []
    values = [part.strip().lower() for part in raw.split(",") if part.strip()]
    invalid = sorted({value for value in values if value not in allowed})
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Geçersiz {field}: {', '.join(invalid)}",
        )
    return list(dict.fromkeys(values))


@router.get("/{case_id}/supply-chain", response_model=dict[str, Any])
async def get_supply_chain(
    case_id: str,
    q: str | None = Query(default=None, max_length=200),
    kind: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    kinds = _parse_csv(kind, NODE_KINDS, "kind")
    statuses = _parse_csv(status_filter, POOL_STATUSES, "status")
    nodes = await list_nodes(db, case_id=case_id, q=q, kinds=kinds, statuses=statuses)
    edges = await list_edges(db, case_id=case_id, statuses=statuses)
    return {
        **graph_data(nodes, edges),
        "kinds": sorted(NODE_KINDS),
        "relations": sorted(RELATIONS),
    }


@router.post("/{case_id}/supply-chain/nodes", status_code=201)
async def add_node(
    case_id: str,
    data: NodeCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    try:
        node, created = await create_node(
            db,
            case_id=case_id,
            kind=validate_node_kind(data.kind),
            name=data.name,
            country=data.country,
            website=data.website,
            identifiers=data.identifiers,
            attributes=data.attributes,
            source="user",
            status="confirmed",
            confidence=data.confidence,
            created_by=getattr(getattr(request.state, "actor", None), "username", None),
        )
    except PoolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(node)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="supply_chain.node.create" if created else "supply_chain.node.merge",
        case_id=case_id,
        target=f"SupplyChainNode: {node.id}",
        details={"kind": node.kind, "status": node.status},
    )
    return node_to_dict(node)


@router.patch("/{case_id}/supply-chain/nodes/{node_id}")
async def update_node(
    case_id: str,
    node_id: str,
    data: NodePatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    node = await _require_node(db, case_id, node_id)
    changed: list[str] = []
    if data.name is not None:
        name = " ".join(data.name.split())
        if len(name) < 2:
            raise HTTPException(status_code=422, detail="ad en az 2 karakter olmalı")
        node.name = name[:256]
        node.normalized = normalize_value("organization", node.name)[:256]
        changed.append("name")
    if data.country is not None:
        node.country = data.country
        changed.append("country")
    if data.website is not None:
        node.website = data.website
        changed.append("website")
    if data.status_value is not None:
        status_value = data.status_value.strip().lower()
        if status_value not in POOL_STATUSES:
            raise HTTPException(status_code=422, detail="geçersiz durum")
        node.status = status_value
        changed.append("status")
    if data.confidence is not None:
        node.confidence = data.confidence
        changed.append("confidence")
    if data.attributes is not None:
        merged = dict(node.attributes or {})
        merged.update(data.attributes)
        node.attributes = merged
        changed.append("attributes")
    if data.identifiers is not None:
        merged = dict(node.identifiers or {})
        merged.update(data.identifiers)
        node.identifiers = merged
        changed.append("identifiers")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="aynı türde bu adla bir düğüm zaten var"
        ) from None
    await db.refresh(node)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="supply_chain.node.update",
        case_id=case_id,
        target=f"SupplyChainNode: {node.id}",
        details={"changed_fields": changed, "status": node.status},
    )
    return node_to_dict(node)


@router.delete("/{case_id}/supply-chain/nodes/{node_id}", status_code=204)
async def delete_node(
    case_id: str,
    node_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    node = await _require_node(db, case_id, node_id)
    name = node.name
    # SQLite testlerinde FK cascade varsayılan kapalı olabilir; ilişkileri
    # açıkça silmek her iki lehçede aynı davranışı verir.
    await db.execute(
        delete(SupplyChainEdge).where(
            SupplyChainEdge.case_id == case_id,
            or_(
                SupplyChainEdge.source_id == node.id,
                SupplyChainEdge.target_id == node.id,
            ),
        )
    )
    await db.delete(node)
    await db.commit()
    await record_request_audit_safe(
        db=db,
        request=request,
        action="supply_chain.node.delete",
        case_id=case_id,
        target=f"SupplyChainNode: {node_id}",
        details={"name": name},
    )
    return None


@router.post("/{case_id}/supply-chain/edges", status_code=201)
async def add_edge(
    case_id: str,
    data: EdgeCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    try:
        edge, created = await create_edge(
            db,
            case_id=case_id,
            source_id=data.source_id,
            target_id=data.target_id,
            relation=validate_relation(data.relation),
            label=data.label,
            confidence=data.confidence,
            status="confirmed",
            source="user",
            evidence=data.evidence,
            created_by=getattr(getattr(request.state, "actor", None), "username", None),
        )
    except PoolValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(edge)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="supply_chain.edge.create" if created else "supply_chain.edge.merge",
        case_id=case_id,
        target=f"SupplyChainEdge: {edge.id}",
        details={"relation": edge.relation, "status": edge.status},
    )
    return edge_to_dict(edge)


@router.patch("/{case_id}/supply-chain/edges/{edge_id}")
async def update_edge(
    case_id: str,
    edge_id: str,
    data: EdgePatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    edge = await _require_edge(db, case_id, edge_id)
    changed: list[str] = []
    if data.label is not None:
        edge.label = data.label
        changed.append("label")
    if data.status_value is not None:
        status_value = data.status_value.strip().lower()
        if status_value not in POOL_STATUSES:
            raise HTTPException(status_code=422, detail="geçersiz durum")
        edge.status = status_value
        changed.append("status")
    if data.confidence is not None:
        edge.confidence = data.confidence
        changed.append("confidence")
    if data.evidence is not None:
        merged = dict(edge.evidence or {})
        merged.update(data.evidence)
        edge.evidence = merged
        changed.append("evidence")
    await db.commit()
    await db.refresh(edge)
    await record_request_audit_safe(
        db=db,
        request=request,
        action="supply_chain.edge.update",
        case_id=case_id,
        target=f"SupplyChainEdge: {edge.id}",
        details={"changed_fields": changed, "status": edge.status},
    )
    return edge_to_dict(edge)


@router.delete("/{case_id}/supply-chain/edges/{edge_id}", status_code=204)
async def delete_edge(
    case_id: str,
    edge_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    edge = await _require_edge(db, case_id, edge_id)
    relation = edge.relation
    await db.delete(edge)
    await db.commit()
    await record_request_audit_safe(
        db=db,
        request=request,
        action="supply_chain.edge.delete",
        case_id=case_id,
        target=f"SupplyChainEdge: {edge_id}",
        details={"relation": relation},
    )
    return None

"""Tedarik zinciri modülü — düğüm, ilişki ve grafik verisi.

Kişi kimlik grafiğinden (`entities` / `edges`) bilinçli olarak ayrıdır:
tedarik zinciri düğümleri şirket/tedarikçi/ürün gibi varlıkları ve yönlü
ilişkileri taşır. Aynı depoyu kirletmemek, kimlik çözümleyicisinin
skorlarını etkilememek için ayrı tablolar kullanılır.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from showis.db.models import SupplyChainEdge, SupplyChainNode
from showis.services.pool import (
    POOL_SOURCES,
    POOL_STATUSES,
    PoolValidationError,
    normalize_value,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

NODE_KINDS: frozenset[str] = frozenset(
    {"company", "supplier", "product", "person", "location", "domain", "other"}
)
RELATIONS: frozenset[str] = frozenset(
    {
        "supplies",
        "manufactures",
        "distributes",
        "subsidiary_of",
        "owns",
        "partner_of",
        "depends_on",
        "located_in",
        "related_to",
    }
)
MAX_NAME = 256
MAX_TEXT = 4000


def validate_node_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value not in NODE_KINDS:
        raise PoolValidationError(f"gecersiz dugum turu: {kind}")
    return value


def validate_relation(relation: str) -> str:
    value = str(relation or "").strip().lower()
    if value not in RELATIONS:
        raise PoolValidationError(f"gecersiz iliski: {relation}")
    return value


def _clean_name(name: str) -> str:
    text = " ".join(str(name or "").split())
    if len(text) < 2:
        raise PoolValidationError("ad en az 2 karakter olmali")
    return text[:MAX_NAME]


async def create_node(
    db: AsyncSession,
    *,
    case_id: str,
    kind: str,
    name: str,
    country: str | None = None,
    website: str | None = None,
    identifiers: dict[str, Any] | None = None,
    attributes: dict[str, Any] | None = None,
    source: str = "user",
    status: str | None = None,
    confidence: float = 0.0,
    created_by: str | None = None,
) -> tuple[SupplyChainNode, bool]:
    kind = validate_node_kind(kind)
    clean_name = _clean_name(name)
    normalized = normalize_value("organization", clean_name)[:MAX_NAME]
    source = str(source or "user").strip().lower()
    if source not in POOL_SOURCES:
        raise PoolValidationError(f"gecersiz kaynak: {source}")
    status = str(status or ("confirmed" if source == "user" else "candidate")).strip().lower()
    if status not in POOL_STATUSES:
        raise PoolValidationError(f"gecersiz durum: {status}")
    if source != "user" and status == "confirmed":
        # Değişmez: AI/tarama/pivot yalnız proposed/candidate yazar.
        status = "proposed"
    confidence = max(0.0, min(1.0, float(confidence or 0.0)))

    existing = (
        await db.execute(
            select(SupplyChainNode).where(
                SupplyChainNode.case_id == case_id,
                SupplyChainNode.kind == kind,
                SupplyChainNode.normalized == normalized,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if source == "user" and existing.source != "user":
            existing.source = "user"
            existing.status = "confirmed"
        if country and not existing.country:
            existing.country = country[:64]
        if website and not existing.website:
            existing.website = str(website)[:MAX_TEXT]
        if identifiers:
            merged = dict(existing.identifiers or {})
            merged.update(identifiers)
            existing.identifiers = merged
        if attributes:
            merged = dict(existing.attributes or {})
            merged.update(attributes)
            existing.attributes = merged
        await db.flush()
        return existing, False

    node = SupplyChainNode(
        case_id=case_id,
        kind=kind,
        name=clean_name,
        normalized=normalized,
        country=(country or None) and str(country)[:64],
        website=(website or None) and str(website)[:MAX_TEXT],
        identifiers=dict(identifiers or {}),
        attributes=dict(attributes or {}),
        source=source,
        status=status,
        confidence=confidence,
        created_by=created_by,
    )
    db.add(node)
    await db.flush()
    return node, True


async def create_edge(
    db: AsyncSession,
    *,
    case_id: str,
    source_id: str,
    target_id: str,
    relation: str,
    label: str | None = None,
    confidence: float = 0.0,
    status: str | None = None,
    source: str = "user",
    evidence: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> tuple[SupplyChainEdge, bool]:
    relation = validate_relation(relation)
    if source_id == target_id:
        raise PoolValidationError("dugum kendisiyle iliskilendirilemez")
    nodes = (
        (
            await db.execute(
                select(SupplyChainNode).where(
                    SupplyChainNode.case_id == case_id,
                    SupplyChainNode.id.in_((source_id, target_id)),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(nodes) != 2:
        raise PoolValidationError("kaynak veya hedef dugum bu vakaya ait degil")
    status = str(status or ("confirmed" if source == "user" else "candidate")).strip().lower()
    if status not in POOL_STATUSES:
        raise PoolValidationError(f"gecersiz durum: {status}")
    if source != "user" and status == "confirmed":
        # Değişmez: AI/tarama/pivot yalnız proposed/candidate yazar.
        status = "proposed"
    confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    existing = (
        await db.execute(
            select(SupplyChainEdge).where(
                SupplyChainEdge.case_id == case_id,
                SupplyChainEdge.source_id == source_id,
                SupplyChainEdge.target_id == target_id,
                SupplyChainEdge.relation == relation,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if evidence:
            merged = dict(existing.evidence or {})
            merged.update(evidence)
            existing.evidence = merged
        existing.confidence = max(existing.confidence or 0.0, confidence)
        await db.flush()
        return existing, False

    edge = SupplyChainEdge(
        case_id=case_id,
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        label=(label or None) and str(label)[:256],
        confidence=confidence,
        status=status,
        source=source,
        evidence=dict(evidence or {}),
        created_by=created_by,
    )
    db.add(edge)
    await db.flush()
    return edge, True


async def list_nodes(
    db: AsyncSession,
    *,
    case_id: str,
    q: str | None = None,
    kinds: Iterable[str] | None = None,
    statuses: Iterable[str] | None = None,
) -> list[SupplyChainNode]:
    conditions: list[Any] = [SupplyChainNode.case_id == case_id]
    text = " ".join(str(q or "").split())[:200]
    if text:
        folded = normalize_value("organization", text)
        escaped = (
            folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        conditions.append(
            or_(
                SupplyChainNode.normalized.like(pattern, escape="\\"),
                func.lower(SupplyChainNode.name).like(pattern, escape="\\"),
            )
        )
    kind_list = [k for k in (kinds or []) if k in NODE_KINDS]
    if kind_list:
        conditions.append(SupplyChainNode.kind.in_(kind_list))
    status_list = [s for s in (statuses or []) if s in POOL_STATUSES]
    if status_list:
        conditions.append(SupplyChainNode.status.in_(status_list))
    return list(
        (
            await db.execute(
                select(SupplyChainNode)
                .where(and_(*conditions))
                .order_by(SupplyChainNode.created_at.asc(), SupplyChainNode.name.asc())
            )
        )
        .scalars()
        .all()
    )


async def list_edges(
    db: AsyncSession,
    *,
    case_id: str,
    statuses: Iterable[str] | None = None,
) -> list[SupplyChainEdge]:
    conditions: list[Any] = [SupplyChainEdge.case_id == case_id]
    status_list = [s for s in (statuses or []) if s in POOL_STATUSES]
    if status_list:
        conditions.append(SupplyChainEdge.status.in_(status_list))
    return list(
        (
            await db.execute(
                select(SupplyChainEdge)
                .where(and_(*conditions))
                .order_by(SupplyChainEdge.created_at.asc())
            )
        )
        .scalars()
        .all()
    )


def node_to_dict(node: SupplyChainNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "case_id": node.case_id,
        "kind": node.kind,
        "name": node.name,
        "country": node.country,
        "website": node.website,
        "identifiers": node.identifiers or {},
        "attributes": node.attributes or {},
        "source": node.source,
        "status": node.status,
        "confidence": node.confidence,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


def edge_to_dict(edge: SupplyChainEdge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "case_id": edge.case_id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "relation": edge.relation,
        "label": edge.label,
        "confidence": edge.confidence,
        "status": edge.status,
        "source": edge.source,
        "evidence": edge.evidence or {},
        "created_at": edge.created_at.isoformat() if edge.created_at else None,
    }


def graph_data(
    nodes: Sequence[SupplyChainNode], edges: Sequence[SupplyChainEdge]
) -> dict[str, Any]:
    node_ids = {node.id for node in nodes}
    return {
        "nodes": [node_to_dict(node) for node in nodes],
        "edges": [
            edge_to_dict(edge)
            for edge in edges
            if edge.source_id in node_ids and edge.target_id in node_ids
        ],
    }


def format_summary(
    nodes: Sequence[SupplyChainNode], edges: Sequence[SupplyChainEdge], limit: int = 40
) -> str:
    names = {node.id: node.name for node in nodes}
    lines = [
        f"- {node.kind}: {node.name}"
        + (f" ({node.country})" if node.country else "")
        + f" [durum={node.status}]"
        for node in nodes[:limit]
    ]
    if edges:
        lines.append("İlişkiler:")
        for edge in edges[:limit]:
            source = names.get(edge.source_id, edge.source_id)
            target = names.get(edge.target_id, edge.target_id)
            lines.append(f"- {source} --{edge.relation}--> {target} [durum={edge.status}]")
    return "\n".join(lines)

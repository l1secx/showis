"""Tipli ilişki grafı: ilişki yazımı, kümeleme ve önem sıralı bağlam özeti."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from showis.db.models import PoolItem, PoolRelation
from showis.services.identity_graph import IdentityCluster, compute_identity_clusters
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

RELATION_TYPES: frozenset[str] = frozenset(
    {
        "same_person",
        "owns_account",
        "uses_email",
        "linked_from",
        "located_in",
        "works_at",
        "alias_of",
        "contradicts",
    }
)
#: Yönsüz ilişkiler: (src, dst) sıralı saklanır, böylece tek kenar olur.
SYMMETRIC_TYPES: frozenset[str] = frozenset({"same_person", "alias_of", "contradicts"})
#: Kimlik kümesi oluşturan ilişkiler (`compute_identity_clusters` `same_person` bekler).
IDENTITY_TYPES: frozenset[str] = frozenset({"same_person", "alias_of"})
RELATION_STATUSES: frozenset[str] = frozenset({"proposed", "candidate", "confirmed", "rejected"})
RELATION_SOURCES: frozenset[str] = frozenset({"user", "rule", "ai", "pivot"})
#: Kullanıcı dışı kaynakların yazabileceği durumlar (invariant).
NON_USER_STATUSES: frozenset[str] = frozenset({"proposed", "candidate"})
MAX_EVIDENCE = 20


class RelationError(ValueError):
    pass


def relation_to_dict(row: PoolRelation) -> dict[str, Any]:
    return {
        "id": row.id,
        "case_id": row.case_id,
        "src_item_id": row.src_item_id,
        "dst_item_id": row.dst_item_id,
        "type": row.type,
        "confidence": float(row.confidence or 0.0),
        "evidence": list(row.evidence or []),
        "status": row.status,
        "source": row.source,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _clean_evidence(evidence: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for entry in evidence or []:
        if not isinstance(entry, dict):
            continue
        cleaned.append(
            {
                "rule": str(entry.get("rule") or "")[:64],
                "source": str(entry.get("source") or "")[:300],
                "weight": float(entry.get("weight") or 0.0),
                "note": str(entry.get("note") or "")[:300],
            }
        )
    return cleaned[:MAX_EVIDENCE]


def independent_sources(evidence: Sequence[dict[str, Any]]) -> int:
    """Bağımsız kaynak sayısı: aynı (kural, kaynak) çifti bir kez sayılır."""
    return len({(e.get("rule"), e.get("source")) for e in evidence if e.get("weight", 0) > 0})


async def upsert_relation(
    db: AsyncSession,
    *,
    case_id: str,
    src_item_id: str,
    dst_item_id: str,
    type: str,
    confidence: float = 0.0,
    evidence: Iterable[dict[str, Any]] | None = None,
    status: str | None = None,
    source: str = "rule",
    created_by: str | None = None,
) -> tuple[PoolRelation, bool]:
    """İlişkiyi ekler/günceller. Kullanıcı dışı kaynak `confirmed` yazamaz."""
    if type not in RELATION_TYPES:
        raise RelationError(f"geçersiz ilişki türü: {type}")
    if source not in RELATION_SOURCES:
        raise RelationError(f"geçersiz kaynak: {source}")
    if src_item_id == dst_item_id:
        raise RelationError("öğe kendisiyle ilişkilendirilemez")
    if type in SYMMETRIC_TYPES and src_item_id > dst_item_id:
        src_item_id, dst_item_id = dst_item_id, src_item_id

    items = (
        (
            await db.execute(
                select(PoolItem.id).where(
                    PoolItem.case_id == case_id,
                    PoolItem.id.in_((src_item_id, dst_item_id)),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(set(items)) != 2:
        raise RelationError("ilişki uçları bu vakanın havuzunda olmalı")

    next_status = (status or "proposed").strip().lower()
    if next_status not in RELATION_STATUSES:
        raise RelationError(f"geçersiz durum: {status}")
    if source != "user" and next_status not in NON_USER_STATUSES:
        # Değişmez: AI/kural/pivot yalnız proposed/candidate yazar.
        next_status = "proposed"

    clean_evidence = _clean_evidence(evidence)
    existing = (
        await db.execute(
            select(PoolRelation).where(
                PoolRelation.case_id == case_id,
                PoolRelation.src_item_id == src_item_id,
                PoolRelation.dst_item_id == dst_item_id,
                PoolRelation.type == type,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        merged = {(e["rule"], e["source"]): e for e in (existing.evidence or [])}
        for entry in clean_evidence:
            merged[(entry["rule"], entry["source"])] = entry
        existing.evidence = list(merged.values())[:MAX_EVIDENCE]
        existing.confidence = max(float(existing.confidence or 0.0), float(confidence or 0.0))
        # Kullanıcı kararı (confirmed/rejected) kural yeniden çalışınca ezilmez;
        # proposed → candidate yükselmesine izin verilir.
        if existing.status == "proposed" and next_status == "candidate":
            existing.status = "candidate"
        return existing, False
    row = PoolRelation(
        case_id=case_id,
        src_item_id=src_item_id,
        dst_item_id=dst_item_id,
        type=type,
        confidence=max(0.0, min(1.0, float(confidence or 0.0))),
        evidence=clean_evidence,
        status=next_status,
        source=source,
        created_by=created_by,
    )
    db.add(row)
    await db.flush()
    return row, True


async def delete_relations_for_item(db: AsyncSession, item_id: str) -> None:
    await db.execute(
        delete(PoolRelation).where(
            or_(PoolRelation.src_item_id == item_id, PoolRelation.dst_item_id == item_id)
        )
    )


@dataclass
class _Node:
    id: str
    platform: str
    evidence_ids: tuple[str, ...] = ()


@dataclass
class _Edge:
    source_entity_id: str
    target_entity_id: str
    type: str
    confidence: float
    evidence_ids: tuple[str, ...]
    reason: str


def identity_clusters(
    items: Sequence[PoolItem], relations: Sequence[PoolRelation]
) -> list[IdentityCluster]:
    """Havuz ilişkilerinden kimlik kümeleri (mevcut union-find yeniden kullanılır).

    Reddedilmiş öğe/ilişkiler dışarıda bırakılır; `same_person`/`alias_of` kenarları
    kümeyi birleştirir.
    """
    live = {item.id: item for item in items if item.status != "rejected"}
    nodes = [_Node(id=item.id, platform=item.kind) for item in live.values()]
    edges: list[_Edge] = []
    for rel in relations:
        if rel.status == "rejected" or rel.type not in IDENTITY_TYPES:
            continue
        if rel.src_item_id not in live or rel.dst_item_id not in live:
            continue
        edges.append(
            _Edge(
                source_entity_id=rel.src_item_id,
                target_entity_id=rel.dst_item_id,
                # `compute_identity_clusters` yalnız same_person/cross_link tanır.
                type="same_person",
                confidence=float(rel.confidence or 0.0),
                evidence_ids=tuple(
                    f"{e.get('rule')}:{e.get('source')}" for e in (rel.evidence or [])
                ),
                reason=", ".join(sorted({str(e.get("rule")) for e in (rel.evidence or [])})),
            )
        )
    _, clusters = compute_identity_clusters(nodes, edges)
    return clusters


_STATUS_RANK = {"confirmed": 3, "candidate": 2, "proposed": 1, "rejected": 0}


def graph_summary(
    items: Sequence[PoolItem], relations: Sequence[PoolRelation], *, limit: int = 40
) -> str:
    """Önem sıralı graf özeti: onaylı öğe/ilişkiler önce, ardından derece ve güven.

    LLM bağlamı için `format_context` metin dökümünün yerine/yanına kullanılır.
    """
    by_id = {item.id: item for item in items if item.status != "rejected"}
    degree: dict[str, int] = {}
    live_relations = [
        rel
        for rel in relations
        if rel.status != "rejected" and rel.src_item_id in by_id and rel.dst_item_id in by_id
    ]
    for rel in live_relations:
        degree[rel.src_item_id] = degree.get(rel.src_item_id, 0) + 1
        degree[rel.dst_item_id] = degree.get(rel.dst_item_id, 0) + 1
    live_relations.sort(
        key=lambda rel: (
            -_STATUS_RANK.get(rel.status, 0),
            -float(rel.confidence or 0.0),
            rel.id,
        )
    )
    lines: list[str] = []
    for rel in live_relations[:limit]:
        src, dst = by_id[rel.src_item_id], by_id[rel.dst_item_id]
        lines.append(
            f"- [{rel.status}] {src.kind}:{src.value[:80]} --{rel.type}--> "
            f"{dst.kind}:{dst.value[:80]} (güven {float(rel.confidence or 0):.2f}, "
            f"kanıt {len(rel.evidence or [])})"
        )
    return "\n".join(lines)

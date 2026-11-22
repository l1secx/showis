"""Kimlik kümeleri — bulgu ve grafik görünümlerinin ortak temeli.

Bir vakadaki entity'ler `SAME_PERSON` / `CROSS_LINK` kenarlarıyla bağlanır.
Analist için anlamlı birim tek tek hesaplar değil, **aynı kişiye ait hesap
kümesi**dir (ör. aynı kullanıcı adı 7 platformda). Bu modül:

* bağlı bileşenleri (union-find) deterministik kümelere çevirir,
* küme başına platform/kanıt/azami güven özeti üretir,
* bulgu API'si ve grafik API'si aynı `cluster_id` değerini kullanır.

Kümeler yalnızca kimlik sinyali taşıyan kenarları dikkate alır; zayıf
`MENTIONS`/`HAS_ACCOUNT` kenarları iki hesabı aynı kişi saymaz.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

IDENTITY_EDGE_TYPES = frozenset({"same_person", "cross_link"})


@dataclass(frozen=True)
class IdentityCluster:
    """Bağlı bileşen özeti (entity_id -> küme eşlemesi için kullanılır)."""

    id: str
    entity_ids: tuple[str, ...]
    size: int
    platforms: tuple[str, ...]
    max_confidence: float
    edge_count: int
    evidence_count: int
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "size": self.size,
            "platforms": list(self.platforms),
            "max_confidence": self.max_confidence,
            "edge_count": self.edge_count,
            "evidence_count": self.evidence_count,
            "reasons": list(self.reasons),
        }


def cluster_id_for(entity_ids: Iterable[str]) -> str:
    """Deterministik, kısa küme kimliği (DB'ye yazılmaz; yanıt alanı)."""
    material = "|".join(sorted(str(entity_id) for entity_id in entity_ids))
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]
    return f"cluster:{digest}"


def _find(parent: dict[str, str], value: str) -> str:
    root = value
    while parent[root] != root:
        root = parent[root]
    # Yol sıkıştırma
    while parent[value] != value:
        parent[value], value = root, parent[value]
    return root


def _union(parent: dict[str, str], left: str, right: str) -> None:
    left_root = _find(parent, left)
    right_root = _find(parent, right)
    if left_root == right_root:
        return
    # Deterministik kök seçimi: küçük id kök olur.
    if left_root <= right_root:
        parent[right_root] = left_root
    else:
        parent[left_root] = right_root


def compute_identity_clusters(
    entities: Iterable,
    edges: Iterable,
) -> tuple[dict[str, IdentityCluster], list[IdentityCluster]]:
    """Entity + edge listesinden küme haritası ve özet listesi üretir.

    Args:
        entities: `id`, `platform`, `evidence_ids` alanları olan nesneler
            (DB `Entity` veya kontrat `Entity`).
        edges: `source_entity_id`, `target_entity_id`, `type`, `confidence`,
            `evidence_ids` alanları olan nesneler (DB `Edge` veya kontrat).

    Returns:
        ``(by_entity, clusters)``. Tek başına kalan entity'ler de bir küme
        olarak döner (`size=1`); istemci yalnız `size > 1` olanları gösterir.
    """
    entity_rows = list(entities)
    if not entity_rows:
        return {}, []

    parent: dict[str, str] = {}
    platform_by_entity: dict[str, str] = {}
    evidence_by_entity: dict[str, set[str]] = {}
    for entity in entity_rows:
        entity_id = str(entity.id)
        parent[entity_id] = entity_id
        platform_by_entity[entity_id] = str(getattr(entity, "platform", "") or "")
        evidence_by_entity[entity_id] = {
            str(item) for item in (getattr(entity, "evidence_ids", None) or [])
        }

    edge_rows = []
    for edge in edges:
        edge_type = str(getattr(edge, "type", "") or "").lower()
        if edge_type not in IDENTITY_EDGE_TYPES:
            continue
        source = str(getattr(edge, "source_entity_id", "") or "")
        target = str(getattr(edge, "target_entity_id", "") or "")
        if source not in parent or target not in parent or source == target:
            continue
        _union(parent, source, target)
        edge_rows.append(
            (
                source,
                target,
                float(getattr(edge, "confidence", 0.0) or 0.0),
                {str(item) for item in (getattr(edge, "evidence_ids", None) or [])},
                str(getattr(edge, "reason", "") or "").strip(),
            )
        )

    members_by_root: dict[str, list[str]] = {}
    for entity_id in parent:
        members_by_root.setdefault(_find(parent, entity_id), []).append(entity_id)

    # Her kök için kenar özeti
    edges_by_root: dict[str, list[tuple[float, set[str]]]] = {}
    reasons_by_root: dict[str, Counter[str]] = {}
    for source, _target, confidence, evidence_ids, reason in edge_rows:
        root = _find(parent, source)
        edges_by_root.setdefault(root, []).append((confidence, evidence_ids))
        if reason:
            reasons_by_root.setdefault(root, Counter())[reason] += 1

    by_entity: dict[str, IdentityCluster] = {}
    clusters: list[IdentityCluster] = []
    for root, members in members_by_root.items():
        members.sort()
        cluster_edges = edges_by_root.get(root, [])
        platforms = tuple(
            sorted({platform_by_entity[m] for m in members if platform_by_entity[m]})
        )
        evidence: set[str] = set()
        for member in members:
            evidence.update(evidence_by_entity.get(member, set()))
        for _, edge_evidence in cluster_edges:
            evidence.update(edge_evidence)
        reason_counter = reasons_by_root.get(root, Counter())
        top_reasons = tuple(
            reason for reason, _count in reason_counter.most_common(3)
        )
        cluster = IdentityCluster(
            id=cluster_id_for(members),
            entity_ids=tuple(members),
            size=len(members),
            platforms=platforms,
            max_confidence=max((confidence for confidence, _ in cluster_edges), default=0.0),
            edge_count=len(cluster_edges),
            evidence_count=len(evidence),
            reasons=top_reasons,
        )
        clusters.append(cluster)
        for member in members:
            by_entity[member] = cluster

    clusters.sort(key=lambda item: (-item.size, item.id))
    return by_entity, clusters


#: Bir bulgunun güven kırılımındaki bağımsız sinyaller. `total` ve negatif
#: `contradiction` sayılmaz; destekleyici kanıt adedi analiste gösterilir.
CORROBORATION_KEYS: tuple[str, ...] = (
    "username_exact_match",
    "handle_exact",
    "name_similarity",
    "cross_link",
    "bio_location_company",
    "commit_email_or_pgp",
    "avatar_exact_hash",
)


def corroboration_signals(breakdown: dict | None) -> list[str]:
    """Güven kırılımında **pozitif** katkı veren sinyal adları."""
    if not breakdown:
        return []
    signals: list[str] = []
    for key in CORROBORATION_KEYS:
        try:
            value = float(breakdown.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            signals.append(key)
    return signals

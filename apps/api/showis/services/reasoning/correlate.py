"""Deterministik korelasyon kuralları (SpiderFoot tarzı) ve tutarlılık denetimi.

Kurallar Python'da tanımlıdır (`RULES`); her kural bir `Match` listesi döndürür.
Eşleşmeler (src, dst, type) bazında birleştirilir; bir ilişki ancak **en az iki
bağımsız kanıt** varsa `candidate` olur, aksi halde `proposed` kalır. `confirmed`
yalnız kullanıcıdan gelir.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from showis.db.models import PoolItem, PoolRelation
from showis.services.reasoning.graph import (
    IDENTITY_TYPES,
    identity_clusters,
    independent_sources,
    upsert_relation,
)
from showis.services.reasoning.scoring import score
from showis.services.research_playbook import is_aggregator_url
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MIN_HANDLE_LEN = 4
MAX_GROUP = 8
#: Aday (candidate) olmak için gereken bağımsız kanıt sayısı.
CANDIDATE_MIN_SOURCES = 2


@dataclass(frozen=True)
class Match:
    src: str
    dst: str
    type: str
    weight: float
    rule: str
    source: str
    note: str = ""


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    weight: float
    apply: Callable[[Sequence[PoolItem]], list[Match]]


def _live(items: Sequence[PoolItem]) -> list[PoolItem]:
    return [item for item in items if item.status != "rejected"]


def _attr(item: PoolItem, key: str) -> str:
    return str((item.attributes or {}).get(key) or "").strip()


def _handle_of(item: PoolItem) -> str:
    if item.kind == "username":
        return item.normalized.lstrip("@")
    if item.kind == "url":
        return _attr(item, "username").casefold().lstrip("@")
    return ""


def _platform_of(item: PoolItem) -> str:
    return _attr(item, "platform").casefold() or item.kind


def _pairs(ids: list[str]) -> list[tuple[str, str]]:
    return list(itertools.combinations(sorted(ids), 2))


def rule_same_handle(items: Sequence[PoolItem]) -> list[Match]:
    """Aynı (yeterince ayırt edici) kullanıcı adı farklı platformlarda → aynı kişi."""
    groups: dict[str, list[PoolItem]] = defaultdict(list)
    for item in _live(items):
        handle = _handle_of(item)
        if len(handle) >= MIN_HANDLE_LEN:
            groups[handle].append(item)
    matches: list[Match] = []
    for handle, members in groups.items():
        if len(members) < 2 or len(members) > MAX_GROUP:
            continue
        for left, right in itertools.combinations(sorted(members, key=lambda m: m.id), 2):
            if _platform_of(left) == _platform_of(right):
                continue
            matches.append(
                Match(
                    left.id,
                    right.id,
                    "same_person",
                    2.0,
                    "same_handle",
                    handle,
                    f"aynı kullanıcı adı: {handle}",
                )
            )
    return matches


def rule_username_email(items: Sequence[PoolItem]) -> list[Match]:
    """Kullanıcı adı e-postanın yerel kısmıyla aynı → uses_email (zayıf kanıt)."""
    live = _live(items)
    emails = [i for i in live if i.kind == "email" and "@" in i.normalized]
    matches: list[Match] = []
    for item in live:
        if item.kind != "username":
            continue
        handle = item.normalized.lstrip("@")
        if len(handle) < MIN_HANDLE_LEN:
            continue
        for email in emails:
            if email.normalized.split("@", 1)[0] == handle:
                matches.append(
                    Match(
                        item.id,
                        email.id,
                        "uses_email",
                        1.0,
                        "username_email_local",
                        email.normalized,
                        "kullanıcı adı = e-posta yerel kısmı",
                    )
                )
    return matches


def rule_shared_aggregator(items: Sequence[PoolItem]) -> list[Match]:
    """Aynı bağlantı toplayıcı sayfada (linktree vb.) listelenen hesaplar → aynı kişi."""
    groups: dict[str, list[PoolItem]] = defaultdict(list)
    for item in _live(items):
        if item.kind not in {"url", "username", "email"}:
            continue
        origin = str((item.provenance or {}).get("url") or _attr(item, "source_url")).split("#")[0]
        if origin and is_aggregator_url(origin) and origin.rstrip("/") != item.normalized:
            groups[origin.rstrip("/").casefold()].append(item)
    matches: list[Match] = []
    for origin, members in groups.items():
        if len(members) < 2 or len(members) > MAX_GROUP:
            continue
        for left, right in itertools.combinations(sorted(members, key=lambda m: m.id), 2):
            matches.append(
                Match(
                    left.id,
                    right.id,
                    "same_person",
                    2.5,
                    "shared_aggregator",
                    origin,
                    "aynı bağlantı toplayıcıda listeleniyor",
                )
            )
    return matches


def rule_same_avatar(items: Sequence[PoolItem]) -> list[Match]:
    """Aynı avatar dHash'i → aynı kişi (güçlü kanıt)."""
    groups: dict[str, list[PoolItem]] = defaultdict(list)
    for item in _live(items):
        digest = _attr(item, "avatar_dhash")
        if digest:
            groups[digest].append(item)
    matches: list[Match] = []
    for digest, members in groups.items():
        if len(members) < 2 or len(members) > MAX_GROUP:
            continue
        for left, right in itertools.combinations(sorted(members, key=lambda m: m.id), 2):
            matches.append(
                Match(
                    left.id,
                    right.id,
                    "same_person",
                    3.0,
                    "same_avatar",
                    digest,
                    "aynı avatar özeti",
                )
            )
    return matches


def rule_profile_owner(items: Sequence[PoolItem]) -> list[Match]:
    """Profil URL'si ve kullanıcı adı aynı platform/hesabı gösteriyor → owns_account."""
    live = _live(items)
    users = {(_platform_of(i), i.normalized.lstrip("@")): i for i in live if i.kind == "username"}
    matches: list[Match] = []
    for item in live:
        if item.kind != "url":
            continue
        key = (_platform_of(item), _attr(item, "username").casefold().lstrip("@"))
        owner = users.get(key)
        if owner is not None and key[1]:
            matches.append(
                Match(
                    owner.id,
                    item.id,
                    "owns_account",
                    1.5,
                    "profile_owner",
                    item.normalized,
                    "profil bağlantısı kullanıcı adıyla eşleşiyor",
                )
            )
    return matches


RULES: tuple[Rule, ...] = (
    Rule("same_handle", "Aynı kullanıcı adı farklı platformlarda", 2.0, rule_same_handle),
    Rule("username_email_local", "Kullanıcı adı = e-posta yerel kısmı", 1.0, rule_username_email),
    Rule(
        "shared_aggregator",
        "Aynı bağlantı toplayıcıda listeleniyor",
        2.5,
        rule_shared_aggregator,
    ),
    Rule("same_avatar", "Aynı avatar özeti", 3.0, rule_same_avatar),
    Rule("profile_owner", "Profil bağlantısı ↔ kullanıcı adı", 1.5, rule_profile_owner),
)


def run_rules(items: Sequence[PoolItem]) -> list[Match]:
    matches: list[Match] = []
    for rule in RULES:
        matches.extend(rule.apply(items))
    return matches


@dataclass(frozen=True)
class Contradiction:
    kind: str  # location_conflict | timeline_conflict
    item_ids: tuple[str, str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "item_ids": list(self.item_ids), "message": self.message}


def build_timeline(items: Sequence[PoolItem]) -> list[dict[str, Any]]:
    """Tarihli `event` öğeleri kronolojik sırayla."""
    rows = [
        {
            "item_id": item.id,
            "date": _attr(item, "date"),
            "place": _attr(item, "place"),
            "label": item.label,
            "status": item.status,
        }
        for item in _live(items)
        if item.kind == "event" and _attr(item, "date")
    ]
    rows.sort(key=lambda row: (row["date"], row["item_id"]))
    return rows


def _adjacency(relations: Sequence[PoolRelation]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for rel in relations:
        if rel.status == "rejected" or rel.type == "contradicts":
            continue
        adjacency[rel.src_item_id].add(rel.dst_item_id)
        adjacency[rel.dst_item_id].add(rel.src_item_id)
    return adjacency


def find_contradictions(
    items: Sequence[PoolItem], relations: Sequence[PoolRelation]
) -> list[Contradiction]:
    """Aynı kimlik kümesine bağlı çelişkili konum ve aynı gün farklı yer olayları."""
    by_id = {item.id: item for item in _live(items)}
    clusters = identity_clusters(list(by_id.values()), relations)
    adjacency = _adjacency(relations)
    found: list[Contradiction] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, left: PoolItem, right: PoolItem, message: str) -> None:
        pair = tuple(sorted((left.id, right.id)))
        key = (kind, pair[0], pair[1])
        if key not in seen:
            seen.add(key)
            found.append(Contradiction(kind, (pair[0], pair[1]), message))

    member_sets = [set(c.entity_ids) for c in clusters] or []
    # Kümesiz tek kişi düğümleri de kendi başına bir "küme" sayılır.
    for item_id in by_id:
        if not any(item_id in members for members in member_sets):
            member_sets.append({item_id})
    for members in member_sets:
        neighbours: set[str] = set()
        for member in members:
            neighbours |= adjacency.get(member, set())
        neighbours -= members
        locations = sorted(
            (by_id[n] for n in neighbours if n in by_id and by_id[n].kind == "location"),
            key=lambda i: i.id,
        )
        for left, right in itertools.combinations(locations, 2):
            if left.normalized != right.normalized:
                add(
                    "location_conflict",
                    left,
                    right,
                    f"Aynı kimliğe bağlı farklı konumlar: {left.value} ≠ {right.value}",
                )
        events = sorted(
            (
                by_id[n]
                for n in neighbours
                if n in by_id and by_id[n].kind == "event" and _attr(by_id[n], "date")
            ),
            key=lambda i: i.id,
        )
        for left, right in itertools.combinations(events, 2):
            if (
                _attr(left, "date") == _attr(right, "date")
                and _attr(left, "place")
                and _attr(right, "place")
                and _attr(left, "place").casefold() != _attr(right, "place").casefold()
            ):
                add(
                    "timeline_conflict",
                    left,
                    right,
                    f"Aynı gün ({_attr(left, 'date')}) farklı yerlerde: "
                    f"{_attr(left, 'place')} ≠ {_attr(right, 'place')}",
                )
    return found


async def run_correlation(db: AsyncSession, case_id: str) -> dict[str, Any]:
    """Kuralları çalıştırır; ilişkileri `proposed/candidate` olarak yazar."""
    items = list(
        (await db.execute(select(PoolItem).where(PoolItem.case_id == case_id))).scalars().all()
    )
    matches = run_rules(items)
    grouped: dict[tuple[str, str, str], list[Match]] = defaultdict(list)
    for match in matches:
        src, dst = match.src, match.dst
        if match.type in {"same_person", "alias_of", "contradicts"} and src > dst:
            src, dst = dst, src
        grouped[(src, dst, match.type)].append(match)

    created = updated = promoted = 0
    for (src, dst, rel_type), group in grouped.items():
        evidence = [
            {"rule": m.rule, "source": m.source, "weight": m.weight, "note": m.note} for m in group
        ]
        enough = independent_sources(evidence) >= CANDIDATE_MIN_SOURCES
        status = "candidate" if enough else "proposed"
        previous = (
            await db.execute(
                select(PoolRelation.status).where(
                    PoolRelation.case_id == case_id,
                    PoolRelation.src_item_id == src,
                    PoolRelation.dst_item_id == dst,
                    PoolRelation.type == rel_type,
                )
            )
        ).scalar_one_or_none()
        row, is_new = await upsert_relation(
            db,
            case_id=case_id,
            src_item_id=src,
            dst_item_id=dst,
            type=rel_type,
            confidence=score(evidence, prior=0.1),
            evidence=evidence,
            status=status,
            source="rule",
            created_by="rule:correlate",
        )
        # Birleşik kanıt yeterliyse yükselt (confirmed asla yazılmaz).
        if not is_new:
            merged = row.evidence or []
            row.confidence = max(float(row.confidence or 0), score(merged, prior=0.1))
            if row.status == "proposed" and independent_sources(merged) >= CANDIDATE_MIN_SOURCES:
                row.status = "candidate"
        if previous == "proposed" and row.status == "candidate":
            promoted += 1
        created += int(is_new)
        updated += int(not is_new)
    await db.flush()

    relations = list(
        (await db.execute(select(PoolRelation).where(PoolRelation.case_id == case_id)))
        .scalars()
        .all()
    )
    contradictions = find_contradictions(items, relations)
    for contradiction in contradictions:
        left, right = contradiction.item_ids
        await upsert_relation(
            db,
            case_id=case_id,
            src_item_id=left,
            dst_item_id=right,
            type="contradicts",
            confidence=0.7,
            evidence=[
                {
                    "rule": contradiction.kind,
                    "source": f"{left}|{right}",
                    "weight": 1.5,
                    "note": contradiction.message,
                }
            ],
            status="proposed",
            source="rule",
            created_by="rule:consistency",
        )
    await db.commit()
    return {
        "rules": len(RULES),
        "matches": len(matches),
        "relations_created": created,
        "relations_updated": updated,
        "promoted": promoted,
        "contradictions": [c.to_dict() for c in contradictions],
    }


__all__ = [
    "IDENTITY_TYPES",
    "RULES",
    "Contradiction",
    "Match",
    "build_timeline",
    "find_contradictions",
    "run_correlation",
    "run_rules",
]

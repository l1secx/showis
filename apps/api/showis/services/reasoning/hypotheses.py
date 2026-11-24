"""Hipotez üretimi: skor kanıttan (log-odds), LLM yalnız gerekçe yazar.

Hipotezler kimlik kümelerinden (`same_person`/`alias_of` bileşenleri) türetilir.
`confidence` her zaman `scoring.score` ile hesaplanır; model çıktısındaki sayı
yok sayılır, böylece model kanıt uydurup güveni şişiremez. Hipotezler `proposed`
başlar; `confirmed/rejected` yalnız kullanıcı kararıdır.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from pydantic import BaseModel, Field
from showis.db.models import Hypothesis, PoolItem, PoolRelation
from showis.services.reasoning.graph import identity_clusters, independent_sources
from showis.services.reasoning.scoring import score
from showis.services.research_interactive import llm_structured
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.reasoning")

HYPOTHESIS_STATUSES: frozenset[str] = frozenset({"proposed", "confirmed", "rejected"})
MAX_NEXT_CHECKS = 6


class HypothesisNarrative(BaseModel):
    """LLM'in yazabildiği tek şey: gerekçe ve sonraki kontroller (skor yok)."""

    rationale: str = Field(default="", max_length=2000)
    next_checks: list[str] = Field(default_factory=list, max_length=MAX_NEXT_CHECKS)


def hypothesis_to_dict(row: Hypothesis) -> dict[str, Any]:
    return {
        "id": row.id,
        "case_id": row.case_id,
        "statement": row.statement,
        "confidence": float(row.confidence or 0.0),
        "status": row.status,
        "supporting": list(row.supporting or []),
        "contradicting": list(row.contradicting or []),
        "next_checks": list(row.next_checks or []),
        "rationale": row.rationale or "",
        "item_ids": list(row.item_ids or []),
        "source": row.source,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _flatten(relations: Sequence[PoolRelation]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for rel in relations:
        for evidence in rel.evidence or []:
            entries.append({**evidence, "relation_id": rel.id})
    return entries


def _deterministic_checks(
    supporting: list[dict[str, Any]], contradicting: list[dict[str, Any]]
) -> list[str]:
    checks: list[str] = []
    if independent_sources(supporting) < 2:
        checks.append("Bağımsız ikinci bir kaynakla (farklı sayfa/kural) eşleşmeyi doğrula.")
    for entry in contradicting[:3]:
        note = str(entry.get("note") or "").strip()
        if note:
            checks.append(f"Çelişkiyi çöz: {note}")
    if not checks:
        checks.append("Önerilen ilişkileri havuzda gözden geçirip onaylayın veya reddedin.")
    return checks[:MAX_NEXT_CHECKS]


def _statement(items: Sequence[PoolItem]) -> str:
    parts = [f"{item.kind}:{item.value[:60]}" for item in items[:6]]
    more = f" (+{len(items) - 6})" if len(items) > 6 else ""
    return "Şu öğeler aynı kişiye/kimliğe aittir: " + ", ".join(parts) + more


async def _narrate(
    client: Any,
    statement: str,
    supporting: list[dict[str, Any]],
    contradicting: list[dict[str, Any]],
) -> HypothesisNarrative | None:
    lines = (
        "\n".join(
            f"- {e.get('rule')}: {e.get('note')} (ağırlık {e.get('weight')})"
            for e in supporting[:12]
        )
        or "(yok)"
    )
    cons = "\n".join(f"- {e.get('note')}" for e in contradicting[:6]) or "(yok)"
    prompt = (
        "Aşağıdaki hipotez için YALNIZCA kısa bir Türkçe gerekçe ve sonraki kontrol "
        "adımlarını yaz. Güven puanı verme, yeni kanıt uydurma; yalnız listelenen "
        "kanıtlara dayan.\n\n"
        f"HİPOTEZ: {statement}\n\nDESTEKLEYEN KANITLAR\n{lines}\n\nÇELİŞKİLER\n{cons}\n"
    )
    try:
        return await llm_structured(
            client,
            [
                {"role": "system", "content": "OSINT analisti; yalnız verilen kanıtları yorumla."},
                {"role": "user", "content": prompt},
            ],
            HypothesisNarrative,
            tool_name="submit_rationale",
            description="Hipotez gerekçesini ve sonraki kontrolleri teslim eder.",
            max_tokens=1200,
        )
    except Exception as exc:  # noqa: BLE001 - LLM hatası deterministik sonucu bozmaz
        logger.info("Hipotez gerekçesi alınamadı: %s", type(exc).__name__)
        return None


async def generate_hypotheses(
    db: AsyncSession, *, case_id: str, client: Any = None, actor: str = "rule:hypotheses"
) -> list[Hypothesis]:
    items = list(
        (await db.execute(select(PoolItem).where(PoolItem.case_id == case_id))).scalars().all()
    )
    relations = list(
        (await db.execute(select(PoolRelation).where(PoolRelation.case_id == case_id)))
        .scalars()
        .all()
    )
    by_id = {item.id: item for item in items}
    existing = {
        tuple(sorted(row.item_ids or [])): row
        for row in (
            await db.execute(select(Hypothesis).where(Hypothesis.case_id == case_id))
        ).scalars()
    }
    adjacency: dict[str, set[str]] = {}
    for rel in relations:
        if rel.status == "rejected" or rel.type == "contradicts":
            continue
        adjacency.setdefault(rel.src_item_id, set()).add(rel.dst_item_id)
        adjacency.setdefault(rel.dst_item_id, set()).add(rel.src_item_id)

    out: list[Hypothesis] = []
    for cluster in identity_clusters(items, relations):
        if cluster.size < 2:
            continue
        members = set(cluster.entity_ids)
        intra = [
            rel
            for rel in relations
            if rel.status != "rejected"
            and rel.type in {"same_person", "alias_of"}
            and rel.src_item_id in members
            and rel.dst_item_id in members
        ]
        reach = set(members)
        for member in members:
            reach |= adjacency.get(member, set())
        conflicts = [
            rel
            for rel in relations
            if rel.status != "rejected"
            and rel.type == "contradicts"
            and (rel.src_item_id in reach or rel.dst_item_id in reach)
        ]
        supporting = _flatten(intra)
        contradicting = _flatten(conflicts)
        confidence = score(supporting, contradicting)
        member_items = [by_id[i] for i in sorted(members) if i in by_id]
        statement = _statement(member_items)
        checks = _deterministic_checks(supporting, contradicting)
        rationale = ""
        if client is not None:
            narrative = await _narrate(client, statement, supporting, contradicting)
            if narrative is not None:
                rationale = narrative.rationale.strip()
                checks = list(dict.fromkeys([*narrative.next_checks, *checks]))[:MAX_NEXT_CHECKS]

        key = tuple(sorted(members))
        row = existing.get(key)
        if row is None:
            row = Hypothesis(
                case_id=case_id,
                statement=statement,
                status="proposed",
                item_ids=sorted(members),
                source="ai" if client is not None else "rule",
                created_by=actor,
            )
            db.add(row)
        # Kullanıcı kararı (confirmed/rejected) korunur; skor ve kanıt güncellenir.
        row.confidence = confidence
        row.supporting = supporting[:40]
        row.contradicting = contradicting[:20]
        row.next_checks = checks
        if rationale:
            row.rationale = rationale
        out.append(row)
    await db.commit()
    for row in out:
        await db.refresh(row)
    return out

"""Onay → havuz → araştırma bağlamı ve tedarik zinciri entegrasyonu."""

from __future__ import annotations

import time

import pytest
import test_research_workspace as _workspace
from showis.db.models import PoolItem
from showis.services.pool import context_items, create_item
from showis.services.research import (
    DEPTH_PROFILES,
    SubagentContext,
    _BudgetGate,
    _format_pool_rows,
    _load_supply_chain_context,
    _subagent_system_prompt,
)
from showis.services.research_playbook import build_identity_profile
from showis.services.supply_chain import create_edge, create_node

db_factory = _workspace.db_factory
client = _workspace.client
_seed_case = _workspace._seed_case


def _ctx(supply_chain_text: str = "") -> SubagentContext:
    return SubagentContext(
        index=0,
        role="business_analyst",
        objective="Tedarik zincirini doğrula",
        queries=[],
        urls=[],
        identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
        pool_snapshot=[],
        depth=DEPTH_PROFILES["quick"],
        gate=_BudgetGate(DEPTH_PROFILES["quick"], {}, time.monotonic()),
        client=None,  # type: ignore[arg-type]
        provider=None,  # type: ignore[arg-type]
        seen_queries=set(),
        supply_chain_text=supply_chain_text,
    )


async def _ai_item(db, case_id: str, kind: str, value: str, status: str) -> PoolItem:
    item, _ = await create_item(
        db,
        case_id=case_id,
        kind=kind,
        label=value,
        value=value,
        source="ai",
        status=status,
        confidence=0.8,
    )
    return item


@pytest.mark.asyncio
async def test_ai_service_writes_cannot_set_confirmed(db_factory):
    """AI kaynaklı servis çağrısı `confirmed` isteyemez (invariant servis katmanında)."""
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        item, _ = await create_item(
            db,
            case_id=case_id,
            kind="organization",
            label="AI önerisi",
            value="Gamma Lojistik",
            source="ai",
            status="confirmed",
        )
        acme, _ = await create_node(
            db,
            case_id=case_id,
            kind="company",
            name="Acme Lojistik",
            source="ai",
            status="confirmed",
        )
        beta, _ = await create_node(
            db,
            case_id=case_id,
            kind="company",
            name="Beta Lojistik",
            source="ai",
            status="confirmed",
        )
        edge, _ = await create_edge(
            db,
            case_id=case_id,
            source_id=acme.id,
            target_id=beta.id,
            relation="supplies",
            source="ai",
            status="confirmed",
        )
        assert item.status == "proposed"
        assert acme.status == "proposed"
        assert edge.status == "proposed"
        await db.rollback()


@pytest.mark.asyncio
async def test_patch_approval_rejection_drives_research_context(db_factory, client):
    """PATCH onayı bağlama girer; PATCH reddi ne bağlama ne biyografiye girer."""
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        approved = await _ai_item(db, case_id, "organization", "Onaylı Tedarikçi", "proposed")
        pending = await _ai_item(db, case_id, "organization", "Bekleyen Öneri", "proposed")
        rejected = await _ai_item(db, case_id, "organization", "Reddedilen Öneri", "proposed")
        await db.commit()
        ids = {"approved": approved.id, "pending": pending.id, "rejected": rejected.id}

    for key, status in (("approved", "confirmed"), ("rejected", "rejected")):
        response = await client.patch(
            f"/api/v1/cases/{case_id}/pool/{ids[key]}", json={"status": status}
        )
        assert response.status_code == 200
        assert response.json()["status"] == status

    async with db_factory() as db:
        rows = await context_items(
            db,
            case_id=case_id,
            limit=100,
            statuses=("confirmed", "candidate", "proposed"),
        )
    values = {row.value for row in rows}
    assert "Onaylı Tedarikçi" in values
    assert "Bekleyen Öneri" in values
    assert "Reddedilen Öneri" not in values

    text = _format_pool_rows(
        [
            {"kind": row.kind, "label": row.label, "value": row.value, "status": row.status}
            for row in rows
        ]
    )
    assert "durum=confirmed" in text
    assert "durum=proposed" in text
    assert "Reddedilen Öneri" not in text


@pytest.mark.asyncio
async def test_supply_chain_context_reaches_research_prompts(db_factory):
    """Onaylı/öneri tedarik zinciri düğümleri ajan bağlamına girer; reddedilen girmez."""
    case_id = await _seed_case(db_factory)
    async with db_factory() as db:
        acme, _ = await create_node(
            db, case_id=case_id, kind="company", name="Acme Lojistik", country="Türkiye"
        )
        beta, _ = await create_node(
            db,
            case_id=case_id,
            kind="supplier",
            name="Beta Lojistik",
            source="ai",
            status="proposed",
        )
        await create_node(
            db,
            case_id=case_id,
            kind="supplier",
            name="Gizli Tedarikçi",
            source="ai",
            status="rejected",
        )
        await create_edge(
            db,
            case_id=case_id,
            source_id=acme.id,
            target_id=beta.id,
            relation="supplies",
            label="2026 sözleşmesi",
        )
        hidden, _ = await create_node(
            db,
            case_id=case_id,
            kind="supplier",
            name="Gizli Halka",
            source="ai",
            status="rejected",
        )
        await create_edge(
            db,
            case_id=case_id,
            source_id=acme.id,
            target_id=hidden.id,
            relation="supplies",
        )
        await db.commit()
        text = await _load_supply_chain_context(db, case_id)

    assert "Acme Lojistik" in text
    assert "Beta Lojistik" in text
    assert "Gizli Tedarikçi" not in text
    assert hidden.id not in text
    assert "supplies" in text

    prompt = _subagent_system_prompt(_ctx(text))
    assert "TEDARİK ZİNCİRİ" in prompt
    assert "Acme Lojistik" in prompt
    assert "Gizli Tedarikçi" not in prompt

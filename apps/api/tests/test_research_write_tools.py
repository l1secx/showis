"""Ajan yazma araçları: alt ajan kuyruğa alır, ana döngü tek oturumda uygular."""

from __future__ import annotations

import time
from typing import Any

import pytest
import test_research_interactive as _interactive
import test_research_workspace as _workspace
from showis.db.models import Case as DbCase
from showis.db.models import PoolItem, ResearchSession, SupplyChainEdge, SupplyChainNode
from showis.services.research import (
    DEPTH_PROFILES,
    MAX_QUEUED_WRITES_PER_AGENT,
    ResearchRuntime,
    SubagentContext,
    _apply_write_intents,
    _BudgetGate,
    _run_subagent,
    _subagent_tool,
    _subagent_tool_specs,
)
from showis.services.research_interactive import research_broadcaster
from showis.services.research_playbook import build_identity_profile
from showis_llm import LLMResult, ToolCall
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory


def _ctx(mode: str = "agent", llm: Any = None) -> SubagentContext:
    depth = DEPTH_PROFILES["quick"]
    return SubagentContext(
        index=0,
        role="web_scout",
        objective="x",
        queries=[],
        urls=[],
        identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
        pool_snapshot=[],
        depth=depth,
        gate=_BudgetGate(depth, {}, time.monotonic()),
        client=llm,
        provider=_interactive._Provider(),
        seen_queries=set(),
        session_id="sess-write",
        mode=mode,
    )


def test_write_tools_in_specs_and_start_scan_only_in_agent_mode():
    agent = {t["name"] for t in _subagent_tool_specs("agent")}
    research = {t["name"] for t in _subagent_tool_specs("research")}
    shared = {"add_pool_item", "add_supply_chain_node", "add_supply_chain_edge", "run_pivot"}
    assert shared <= agent and shared <= research
    assert "start_scan" in agent and "start_scan" not in research


@pytest.mark.asyncio
async def test_subagent_queues_writes_without_db():
    ctx = _ctx()
    out = await _subagent_tool(
        ctx,
        "add_pool_item",
        {"kind": "url", "value": "https://example.org/k", "source_url": "https://example.org/k"},
    )
    assert out["queued"] is True
    assert ctx.write_queue[0]["input"]["provenance"]["url"] == "https://example.org/k"
    bad = await _subagent_tool(ctx, "add_pool_item", {"kind": "nope", "value": "x"})
    assert "error" in bad
    assert "error" in await _subagent_tool(ctx, "run_pivot", {"pivot": "email_domain"})
    research_ctx = _ctx("research")
    assert "error" in await _subagent_tool(research_ctx, "start_scan", {})
    for _ in range(MAX_QUEUED_WRITES_PER_AGENT + 2):
        await _subagent_tool(ctx, "add_supply_chain_node", {"name": "Acme"})
    assert len(ctx.write_queue) == MAX_QUEUED_WRITES_PER_AGENT


@pytest.mark.asyncio
async def test_native_loop_queues_write_tool_calls():
    llm = _interactive._NativeClient(
        [
            LLMResult(
                tool_calls=[
                    ToolCall("w1", "add_pool_item", {"kind": "email", "value": "k@example.org"})
                ]
            ),
            LLMResult(tool_calls=[ToolCall("w2", "submit_report", {"summary": "tamam"})]),
        ]
    )
    ctx = _ctx(llm=llm)
    report, _ = await _run_subagent(ctx)
    assert report.summary == "tamam"
    assert [w["tool"] for w in ctx.write_queue] == ["add_pool_item"]


async def _runtime(db, client_, db_factory_, mode: str):
    case_id, session_id = await _interactive._start_session(client_, db_factory_)
    case = (await db.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
    row = (
        await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
    ).scalar_one()
    row.mode = mode
    await db.commit()
    runtime = ResearchRuntime(
        db=db,
        session=row,
        case=case,
        target=None,
        client=None,  # type: ignore[arg-type]
        identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
        pool_items=[],
        stats={"depth": "quick"},
        started=time.monotonic(),
    )
    return runtime, case_id, session_id


@pytest.mark.parametrize("mode,expected", [("research", "proposed"), ("agent", "candidate")])
@pytest.mark.asyncio
async def test_apply_intents_writes_only_proposed_or_candidate(db_factory, client, mode, expected):
    async with db_factory() as db:
        runtime, case_id, session_id = await _runtime(db, client, db_factory, mode)
        queue = research_broadcaster.subscribe(session_id)
        existing = (
            await db.execute(
                select(PoolItem).where(PoolItem.case_id == case_id, PoolItem.kind == "email")
            )
        ).scalars().first()
        intents = [
            {
                "tool": "add_pool_item",
                "input": {
                    "kind": "url",
                    "value": "https://example.org/kerem",
                    "confidence": 0.9,
                    "status": "confirmed",  # yok sayılmalı
                    "provenance": {"url": "https://example.org/kerem", "status": "confirmed"},
                },
                "agent": 0,
                "role": "web_scout",
            },
            {
                "tool": "add_supply_chain_node",
                "input": {"name": "Acme Ltd", "kind": "company"},
                "agent": 0,
                "role": "web_scout",
            },
            {
                "tool": "add_supply_chain_node",
                "input": {"name": "Beta AŞ", "kind": "company"},
                "agent": 0,
                "role": "web_scout",
            },
            {
                "tool": "add_supply_chain_edge",
                "input": {"source": "Acme Ltd", "target": "Beta AŞ", "relation": "supplies"},
                "agent": 0,
                "role": "web_scout",
            },
            {
                "tool": "run_pivot",
                "input": {"item_id": existing.id if existing else "yok", "pivot": "email_domain"},
                "agent": 0,
                "role": "web_scout",
            },
            {"tool": "run_pivot", "input": {"item_id": "yok", "pivot": "x"}, "agent": 0},
        ]
        outcomes = await _apply_write_intents(runtime, intents)
        assert [o["ok"] for o in outcomes][:4] == [True, True, True, True]
        assert outcomes[-1]["ok"] is False
        ai_rows = [
            row
            for row in (await db.execute(select(PoolItem))).scalars()
            if (row.created_by or "").startswith("ai:")
        ]
        assert len(ai_rows) >= 2  # doğrudan öneri + pivot çıktısı
        assert {row.status for row in ai_rows} == {expected}
        nodes = (await db.execute(select(SupplyChainNode))).scalars().all()
        edges = (await db.execute(select(SupplyChainEdge))).scalars().all()
        assert {n.status for n in nodes} == {expected}
        assert {e.status for e in edges} == {expected}
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(e["type"] == "research.proposal" for e in events)


@pytest.mark.asyncio
async def test_start_scan_rejected_outside_agent_mode(db_factory, client):
    async with db_factory() as db:
        runtime, _, _ = await _runtime(db, client, db_factory, "research")
        outcomes = await _apply_write_intents(
            runtime, [{"tool": "start_scan", "input": {}, "agent": 0}]
        )
        assert outcomes[0]["ok"] is False and "tam ajan" in outcomes[0]["error"]

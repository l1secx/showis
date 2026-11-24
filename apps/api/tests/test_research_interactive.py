"""İnteraktif araştırma: native araç döngüsü, steer kuyruğu, ask_user, interrupt/resume."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import test_research_workspace as _workspace
from showis.db.models import PoolItem, ResearchMessage, ResearchSession
from showis.services import research as research_mod
from showis.services.research import (
    DEPTH_PROFILES,
    SubagentContext,
    SubagentReport,
    SubagentStep,
    SupervisorPlan,
    _BudgetGate,
    _run_subagent,
)
from showis.services.research_interactive import (
    ResearchControl,
    get_control,
    llm_structured,
    register_control,
    release_control,
    research_broadcaster,
)
from showis.services.research_playbook import build_identity_profile
from showis_llm import LLMClientError, LLMResult, ToolCall
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory
_seed_case = _workspace._seed_case


class _Hit:
    def __init__(self, title: str, url: str, snippet: str = ""):
        self.title, self.url, self.snippet = title, url, snippet


class _Provider:
    async def search(self, query: str, limit: int = 10):
        return [_Hit("Kerem Boz", "https://github.com/keremboz", "dev")]


class _NativeClient:
    """complete_with_tools döndüren sahte istemci (ağ yok)."""

    model = "fake-native"

    def __init__(self, turns: list[LLMResult]):
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []
        self.native_tools_disabled = False

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        return self.turns.pop(0)

    async def complete(self, **kwargs):  # pragma: no cover - native yolda çağrılmaz
        raise AssertionError("native yolda complete çağrılmamalı")


def _ctx(llm, control: ResearchControl | None = None) -> SubagentContext:
    depth = DEPTH_PROFILES["quick"]
    return SubagentContext(
        index=0,
        role="web_scout",
        objective="keremboz doğrula",
        queries=[],
        urls=[],
        identity=build_identity_profile(first_name="Kerem", last_name="Boz", nickname="keremboz"),
        pool_snapshot=[],
        depth=depth,
        gate=_BudgetGate(depth, {}, time.monotonic()),
        client=llm,
        provider=_Provider(),
        seen_queries=set(),
        session_id="sess-native",
        control=control,
    )


@pytest.mark.asyncio
async def test_subagent_native_tool_loop_returns_report():
    llm = _NativeClient(
        [
            LLMResult(tool_calls=[ToolCall("t1", "web_search", {"query": "keremboz github"})]),
            LLMResult(
                tool_calls=[
                    ToolCall(
                        "t2",
                        "submit_report",
                        {
                            "summary": "GitHub profili bulundu",
                            "findings": [
                                {
                                    "kind": "url",
                                    "label": "GitHub",
                                    "value": "https://github.com/keremboz",
                                    "confidence": 0.7,
                                }
                            ],
                        },
                    )
                ]
            ),
        ]
    )
    queue = research_broadcaster.subscribe("sess-native")
    report, counts = await _run_subagent(_ctx(llm))
    assert report.summary.startswith("GitHub")
    assert any(f.value == "https://github.com/keremboz" for f in report.findings)
    # İkinci turda araç sonucu tool rolüyle geri verilmiş olmalı.
    second = llm.calls[1]
    assert any(m["role"] == "tool" and m["tool_call_id"] == "t1" for m in second)
    types = []
    while not queue.empty():
        types.append(queue.get_nowait()["type"])
    assert "research.tool_start" in types and "research.tool_end" in types
    assert counts["role"] == "web_scout"


class _UnsupportedClient(_NativeClient):
    async def complete_with_tools(self, messages, tools=None, **kwargs):
        raise LLMClientError("LLM HTTP error 400", status_code=400)

    async def complete(self, messages, schema=None, **kwargs):
        return SubagentStep(
            thought="bitir",
            report=SubagentReport(summary="JSON yolu çalıştı"),
        )


@pytest.mark.asyncio
async def test_subagent_falls_back_to_json_schema_when_tools_unsupported():
    llm = _UnsupportedClient([])
    report, _ = await _run_subagent(_ctx(llm))
    assert report.summary == "JSON yolu çalıştı"
    assert llm.native_tools_disabled is True


@pytest.mark.asyncio
async def test_llm_structured_forces_tool_and_falls_back():
    llm = _NativeClient(
        [LLMResult(tool_calls=[ToolCall("p", "submit_plan", {"thought": "x", "queries": ["a"]})])]
    )
    plan = await llm_structured(
        llm, [{"role": "user", "content": "plan"}], SupervisorPlan,
        tool_name="submit_plan", description="d",
    )
    assert plan is not None and plan.queries == ["a"]

    class _Plain:
        async def complete(self, messages, schema=None, **kw):
            return SupervisorPlan(thought="eski yol")

    plan = await llm_structured(
        _Plain(), [{"role": "user", "content": "plan"}], SupervisorPlan,
        tool_name="submit_plan", description="d",
    )
    assert plan is not None and plan.thought == "eski yol"


@pytest.mark.asyncio
async def test_control_ask_and_answer_roundtrip():
    control = ResearchControl(session_id="s")
    seen: list[str] = []

    async def on_question(pending):
        seen.append(pending.question)
        asyncio.get_running_loop().call_soon(control.answer, pending.id, "Ankara'daki Kerem")

    control.on_question = on_question
    answer = await control.ask("Hangi Kerem?", ["Ankara", "İzmir"])
    assert answer == "Ankara'daki Kerem" and seen == ["Hangi Kerem?"]
    assert control.pending is None

    control.request_interrupt()
    assert "durduruldu" in await control.ask("Tekrar?")


@pytest.mark.asyncio
async def test_control_question_limit():
    control = ResearchControl(session_id="s")

    async def on_question(pending):
        control.answer(pending.id, "tamam")

    control.on_question = on_question
    for _ in range(4):
        assert await control.ask("soru") == "tamam"
    assert "sınır" in await control.ask("soru 5")


async def _start_session(client, db_factory) -> tuple[str, str]:
    case_id = await _seed_case(db_factory)
    provider = await client.post(
        "/api/v1/ai/providers",
        json={
            "name": "OpenCode Go",
            "preset": "opencode-go",
            "config": {"base_url": "https://opencode.ai/inference/go/openai/v1"},
            "secret": "sk-test",
            "activate": True,
        },
    )
    assert provider.status_code == 201
    started = await client.post(
        f"/api/v1/cases/{case_id}/research",
        json={"question": "Kerem Boz kimdir? Kamuya açık kaynaklarda doğrula.", "mode": "agent"},
    )
    assert started.status_code == 201, started.text
    return case_id, started.json()["id"]


@pytest.mark.asyncio
async def test_message_while_running_is_queued_not_409(db_factory, client):
    case_id, session_id = await _start_session(client, db_factory)
    control = register_control(session_id)
    try:
        response = await client.post(
            f"/api/v1/cases/{case_id}/research/{session_id}/messages",
            json={"question": "Önce GitHub hesaplarına odaklan"},
        )
        assert response.status_code == 202, response.text
        assert response.json()["queued"] is True
        assert control.steering == ["Önce GitHub hesaplarına odaklan"]
    finally:
        release_control(session_id)
    async with db_factory() as session:
        steer = (
            await session.execute(
                select(ResearchMessage).where(
                    ResearchMessage.session_id == session_id, ResearchMessage.tool_name == "steer"
                )
            )
        ).scalar_one()
    assert steer.role == "user"


@pytest.mark.asyncio
async def test_pending_question_answered_via_endpoint_and_message(db_factory, client):
    case_id, session_id = await _start_session(client, db_factory)
    control = register_control(session_id)
    try:
        task = asyncio.create_task(control.ask("Hangi şehir?", ["Ankara", "İzmir"]))
        await asyncio.sleep(0.01)
        detail = await client.get(f"/api/v1/cases/{case_id}/research/{session_id}")
        pending = detail.json()["pending_question"]
        assert pending["question"] == "Hangi şehir?" and pending["options"] == ["Ankara", "İzmir"]

        wrong = await client.post(
            f"/api/v1/cases/{case_id}/research/{session_id}/answer",
            json={"question_id": "yanlis", "answer": "x"},
        )
        assert wrong.status_code == 409
        ok = await client.post(
            f"/api/v1/cases/{case_id}/research/{session_id}/answer",
            json={"question_id": pending["question_id"], "answer": "Ankara"},
        )
        assert ok.status_code == 200
        assert await task == "Ankara"

        # Sohbetten gelen metin de bekleyen soruyu yanıtlar.
        task = asyncio.create_task(control.ask("Başka soru?"))
        await asyncio.sleep(0.01)
        chat = await client.post(
            f"/api/v1/cases/{case_id}/research/{session_id}/messages",
            json={"question": "evet, aynı kişi"},
        )
        assert chat.json()["answered"] is True
        assert await task == "evet, aynı kişi"
    finally:
        release_control(session_id)


@pytest.mark.asyncio
async def test_ask_hooks_persist_status_and_messages(db_factory, client, monkeypatch):
    case_id, session_id = await _start_session(client, db_factory)

    @asynccontextmanager
    async def fake_task_session():
        async with db_factory() as session:
            yield session

    monkeypatch.setattr(research_mod, "task_session", fake_task_session)
    control = register_control(session_id)
    control.on_question = research_mod._make_question_hook(session_id)
    control.on_answered = research_mod._make_answered_hook(session_id)
    try:
        task = asyncio.create_task(control.ask("Hangi Kerem?", ["A", "B"]))
        await asyncio.sleep(0.05)
        async with db_factory() as session:
            row = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == session_id)
                )
            ).scalar_one()
            assert row.status == "awaiting_user"
        # awaiting_user durumunda ikinci oturum açılamaz.
        conflict = await client.post(
            f"/api/v1/cases/{case_id}/research",
            json={"question": "İkinci araştırma sorusu denemesi", "mode": "research"},
        )
        assert conflict.status_code == 409
        control.answer(control.pending.id, "A")
        assert await task == "A"
        async with db_factory() as session:
            row = (
                await session.execute(
                    select(ResearchSession).where(ResearchSession.id == session_id)
                )
            ).scalar_one()
            assert row.status == "running"
            tools = [
                m.tool_name
                for m in (
                    await session.execute(
                        select(ResearchMessage).where(ResearchMessage.session_id == session_id)
                    )
                ).scalars()
            ]
        assert "ask_user" in tools and "answer" in tools
    finally:
        release_control(session_id)


@pytest.mark.asyncio
async def test_interrupt_and_resume_endpoints(db_factory, client):
    case_id, session_id = await _start_session(client, db_factory)
    control = register_control(session_id)
    try:
        interrupted = await client.post(f"/api/v1/cases/{case_id}/research/{session_id}/interrupt")
        assert interrupted.status_code == 200
        assert interrupted.json()["status"] == "interrupting"
        assert control.interrupt is True
    finally:
        release_control(session_id)
    assert get_control(session_id) is None

    # Çalışan görev yoksa (bayat durum) doğrudan interrupted olur ve devam ettirilebilir.
    stale = await client.post(f"/api/v1/cases/{case_id}/research/{session_id}/interrupt")
    assert stale.json()["status"] == "interrupted"
    # Durdurulmuş oturum yer tutmaz; ama resume yalnız interrupted için çalışır.
    resumed = await client.post(f"/api/v1/cases/{case_id}/research/{session_id}/resume")
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"
    again = await client.post(f"/api/v1/cases/{case_id}/research/{session_id}/resume")
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_persist_emits_proposals_and_never_confirms(db_factory, client):
    case_id, session_id = await _start_session(client, db_factory)
    from showis.db.models import Case as DbCase
    from showis.services.research import FindingDraft, ResearchRuntime

    queue = research_broadcaster.subscribe(session_id)
    async with db_factory() as session:
        case = (await session.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
        row = (
            await session.execute(select(ResearchSession).where(ResearchSession.id == session_id))
        ).scalar_one()
        runtime = ResearchRuntime(
            db=session,
            session=row,
            case=case,
            target=None,
            client=None,  # type: ignore[arg-type]
            identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
            pool_items=[],
            stats={"depth": "quick"},
            started=time.monotonic(),
        )
        runtime.add_draft(
            FindingDraft(kind="url", label="GitHub", value="https://example.org/kerem")
        )
        await runtime.persist()
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert [e["type"] for e in events] == ["research.proposal"], events
    proposal = events[0]
    assert proposal["data"]["status"] == "candidate"  # agent modu
    assert proposal["data"]["item_id"]

    listed = await client.get(f"/api/v1/cases/{case_id}/research/{session_id}/proposals")
    assert listed.status_code == 200
    assert [i["value"] for i in listed.json()["items"]] == ["https://example.org/kerem"]
    async with db_factory() as session:
        statuses = {
            i.status
            for i in (
                await session.execute(select(PoolItem).where(PoolItem.source == "ai"))
            ).scalars()
        }
    assert "confirmed" not in statuses


def test_alembic_single_head_is_latest():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["0023_watch_alerts"]


@pytest.mark.asyncio
async def test_park_interrupted_saves_resume_state(db_factory, client):
    case_id, session_id = await _start_session(client, db_factory)
    from showis.db.models import Case as DbCase
    from showis.services.research import ResearchRuntime, _park_interrupted

    async with db_factory() as session:
        case = (await session.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
        row = (
            await session.execute(select(ResearchSession).where(ResearchSession.id == session_id))
        ).scalar_one()
        runtime = ResearchRuntime(
            db=session,
            session=row,
            case=case,
            target=None,
            client=None,  # type: ignore[arg-type]
            identity=build_identity_profile(first_name="Kerem", last_name="Boz"),
            pool_items=[],
            stats={"depth": "quick"},
            started=time.monotonic(),
        )
        runtime.query_seen["\"keremboz\""] = "temel"
        runtime.add_lead("username", "kboz", "test")
        await _park_interrupted(runtime, next_wave=2)
        await session.refresh(row)
        assert row.status == "interrupted"
        assert row.stats["resume"]["next_wave"] == 2
        assert row.stats["resume"]["leads"][0]["value"] == "kboz"

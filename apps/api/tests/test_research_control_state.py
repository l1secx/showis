"""Kalıcı kontrol durumu: steer, ask_user ve interrupt DB'de; yeniden başlatmadan sağ çıkar."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
import test_research_interactive as _interactive
import test_research_workspace as _workspace
from showis.db.models import ResearchMessage, ResearchSession
from showis.services import research as research_mod
from showis.services import research_state
from showis.services.research_interactive import (
    ResearchControl,
    get_control,
    register_control,
    release_control,
)
from sqlalchemy import select

client = _workspace.client
db_factory = _workspace.db_factory


async def _session_row(db_factory, session_id: str) -> ResearchSession:
    async with db_factory() as db:
        return (
            await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
        ).scalar_one()


@pytest.mark.asyncio
async def test_steer_and_interrupt_are_persisted(db_factory, client):
    case_id, session_id = await _interactive._start_session(client, db_factory)
    register_control(session_id)
    try:
        res = await client.post(
            f"/api/v1/cases/{case_id}/research/{session_id}/messages",
            json={"question": "GitHub'a odaklan"},
        )
        assert res.status_code == 202
        await client.post(f"/api/v1/cases/{case_id}/research/{session_id}/interrupt")
    finally:
        release_control(session_id)
    async with db_factory() as db:
        state = await research_state.get_state(db, session_id)
    assert state is not None
    assert state.steering == ["GitHub'a odaklan"]
    assert state.interrupt is True


@pytest.mark.asyncio
async def test_awaiting_user_survives_restart_and_resumes_on_answer(db_factory, client):
    case_id, session_id = await _interactive._start_session(client, db_factory)
    # "API yeniden başladı": canlı kontrol yok, ama soru DB'de.
    assert get_control(session_id) is None
    async with db_factory() as db:
        row = (
            await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
        ).scalar_one()
        row.status = "awaiting_user"
        await research_state.save_pending_question(
            db, session_id, question_id="q1", question="Hangi Kerem?", options=["A", "B"]
        )
    detail = (await client.get(f"/api/v1/cases/{case_id}/research/{session_id}")).json()
    assert detail["status"] == "awaiting_user"
    assert detail["pending_question"] == {
        "question_id": "q1",
        "question": "Hangi Kerem?",
        "options": ["A", "B"],
    }
    wrong = await client.post(
        f"/api/v1/cases/{case_id}/research/{session_id}/answer",
        json={"question_id": "yanlis", "answer": "A"},
    )
    assert wrong.status_code == 409
    ok = await client.post(
        f"/api/v1/cases/{case_id}/research/{session_id}/answer",
        json={"question_id": "q1", "answer": "A"},
    )
    assert ok.status_code == 200, ok.text
    row = await _session_row(db_factory, session_id)
    assert row.status == "queued"
    async with db_factory() as db:
        state = await research_state.get_state(db, session_id)
        assert state is not None and state.pending_question is None
        assert state.answer and state.answer["answer"] == "A"
        tools = [
            m.tool_name
            for m in (
                await db.execute(
                    select(ResearchMessage).where(ResearchMessage.session_id == session_id)
                )
            ).scalars()
        ]
    assert "answer" in tools
    # Soru kapandıktan sonra yeniden yanıt 409.
    again = await client.post(
        f"/api/v1/cases/{case_id}/research/{session_id}/answer",
        json={"question_id": "q1", "answer": "B"},
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_chat_message_answers_persisted_question(db_factory, client):
    case_id, session_id = await _interactive._start_session(client, db_factory)
    async with db_factory() as db:
        row = (
            await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
        ).scalar_one()
        row.status = "awaiting_user"
        await research_state.save_pending_question(
            db, session_id, question_id="q2", question="Hangi şehir?", options=[]
        )
    res = await client.post(
        f"/api/v1/cases/{case_id}/research/{session_id}/messages",
        json={"question": "Ankara"},
    )
    assert res.status_code == 202 and res.json()["answered"] is True
    assert (await _session_row(db_factory, session_id)).status == "queued"


@pytest.mark.asyncio
async def test_run_start_restores_steering_and_offline_answer(db_factory, client, monkeypatch):
    case_id, session_id = await _interactive._start_session(client, db_factory)
    async with db_factory() as db:
        await research_state.append_steer(db, session_id, "Önce LinkedIn")
        await research_state.save_pending_question(
            db, session_id, question_id="q3", question="Hangi Kerem?", options=[]
        )
        await research_state.store_offline_answer(db, session_id, "q3", "Ankara'daki")
        state = await research_state.get_state(db, session_id)
        assert state is not None
        state.interrupt = True
        state.asked_count = 2
        await db.commit()

    @asynccontextmanager
    async def fake_task_session():
        async with db_factory() as session:
            yield session

    captured: dict[str, Any] = {}

    async def fake_build(*args, **kwargs):
        return object()

    async def fake_loop(db, **kwargs):
        control = get_control(session_id)
        assert control is not None
        captured["steering"] = list(control.steering)
        captured["asked"] = control.asked
        kwargs["session"].status = "completed"

    monkeypatch.setattr(research_mod, "task_session", fake_task_session)
    monkeypatch.setattr(research_mod, "build_llm_client", fake_build)
    monkeypatch.setattr(research_mod, "_run_loop", fake_loop)
    await research_mod.run_research_session(session_id)
    assert captured["steering"][0] == "Önce LinkedIn"
    assert any("Ankara'daki" in note and "Hangi Kerem?" in note for note in captured["steering"])
    assert captured["asked"] == 2
    async with db_factory() as db:
        state = await research_state.get_state(db, session_id)
        assert state is not None
        assert state.interrupt is False and state.answer is None


@pytest.mark.asyncio
async def test_checkpoint_runs_before_question_hook():
    order: list[str] = []
    control = ResearchControl(session_id="s")

    async def checkpoint() -> None:
        order.append("checkpoint")

    async def on_question(pending) -> None:
        order.append("question")
        control.answer(pending.id, "evet")

    control.on_checkpoint = checkpoint
    control.on_question = on_question
    assert await control.ask("Emin misin?") == "evet"
    assert order == ["checkpoint", "question"]


@pytest.mark.asyncio
async def test_checkpoint_saves_resume_state(db_factory, client):
    import time

    from showis.db.models import Case as DbCase
    from showis.services.research import ResearchRuntime, _checkpoint_for_question
    from showis.services.research_playbook import build_identity_profile

    case_id, session_id = await _interactive._start_session(client, db_factory)
    async with db_factory() as db:
        case = (await db.execute(select(DbCase).where(DbCase.id == case_id))).scalar_one()
        row = (
            await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
        ).scalar_one()
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
        runtime.current_wave = 3
        await _checkpoint_for_question(runtime)
    stored = await _session_row(db_factory, session_id)
    assert stored.stats["resume"]["next_wave"] == 3


@pytest.mark.asyncio
async def test_running_session_without_control_marks_interrupted(db_factory, client):
    """B-014: süreç yeniden başlamışsa steer yutulmaz; oturum interrupted olur."""
    case_id = await _workspace._seed_case(db_factory)
    async with db_factory() as db:
        db.add(
            ResearchSession(
                id="stale-running-session",
                case_id=case_id,
                title="Süreçsiz",
                question="Kerem Boz kim?",
                status="running",
                created_by="test",
            )
        )
        await db.commit()
    assert get_control("stale-running-session") is None

    res = await client.post(
        f"/api/v1/cases/{case_id}/research/stale-running-session/messages",
        json={"question": "LinkedIn'e odaklan"},
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "interrupted"
    assert res.json()["queued"] is False

    async with db_factory() as db:
        row = (
            await db.execute(
                select(ResearchSession).where(ResearchSession.id == "stale-running-session")
            )
        ).scalar_one()
        state = await research_state.get_state(db, "stale-running-session")
    assert row.status == "interrupted"
    assert state is not None and "LinkedIn'e odaklan" in state.steering

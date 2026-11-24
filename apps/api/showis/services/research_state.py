"""Araştırma oturumunun kalıcı kontrol durumu (DB).

Bellekteki `ResearchControl` canlı koşu içindir; bu katman steer kuyruğunu,
`ask_user` sorusu/yanıtını ve durdurma bayrağını DB'de tutar. Böylece API
yeniden başlasa da `awaiting_user` oturumu sorusunu korur ve yanıtla sürer.

Değişmez: bu katman havuza yazmaz.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from showis.db.models import ResearchControlState
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_STORED_STEERING = 200
MAX_STEER_CHARS = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_state(db: AsyncSession, session_id: str) -> ResearchControlState | None:
    return (
        await db.execute(
            select(ResearchControlState).where(ResearchControlState.session_id == session_id)
        )
    ).scalar_one_or_none()


async def ensure_state(db: AsyncSession, session_id: str) -> ResearchControlState:
    state = await get_state(db, session_id)
    if state is None:
        state = ResearchControlState(
            session_id=session_id, interrupt=False, steering=[], asked_count=0
        )
        db.add(state)
        await db.flush()
    return state


async def append_steer(db: AsyncSession, session_id: str, text: str) -> bool:
    text = " ".join(str(text or "").split())[:MAX_STEER_CHARS]
    if not text:
        return False
    state = await ensure_state(db, session_id)
    current = list(state.steering or [])
    if len(current) >= MAX_STORED_STEERING:
        return False
    state.steering = [*current, text]
    await db.commit()
    return True


async def set_interrupt(db: AsyncSession, session_id: str, value: bool) -> None:
    state = await ensure_state(db, session_id)
    state.interrupt = value
    await db.commit()


async def save_pending_question(
    db: AsyncSession, session_id: str, *, question_id: str, question: str, options: list[str]
) -> None:
    state = await ensure_state(db, session_id)
    state.pending_question = {
        "id": question_id,
        "question": question,
        "options": list(options),
        "asked_at": _now_iso(),
    }
    state.answer = None
    state.asked_count = int(state.asked_count or 0) + 1
    await db.commit()


async def clear_pending_question(db: AsyncSession, session_id: str) -> None:
    state = await get_state(db, session_id)
    if state is None:
        return
    state.pending_question = None
    await db.commit()


def pending_question_dict(state: ResearchControlState | None) -> dict[str, Any] | None:
    pending = state.pending_question if state is not None else None
    if not pending:
        return None
    return {
        "question_id": pending.get("id"),
        "question": pending.get("question", ""),
        "options": list(pending.get("options") or []),
    }


async def store_offline_answer(
    db: AsyncSession, session_id: str, question_id: str | None, answer: str
) -> dict[str, Any] | None:
    """Süreç yokken verilen yanıtı saklar ve soruyu kapatır; yoksa None döner."""
    state = await get_state(db, session_id)
    pending = state.pending_question if state is not None else None
    if state is None or not pending:
        return None
    if question_id and pending.get("id") != question_id:
        return None
    stored = {
        "question_id": pending.get("id"),
        "question": pending.get("question", ""),
        "answer": " ".join(str(answer or "").split())[:2000] or "(boş yanıt)",
        "answered_at": _now_iso(),
    }
    state.answer = stored
    state.pending_question = None
    await db.commit()
    return stored


async def consume_answer(db: AsyncSession, session_id: str) -> dict[str, Any] | None:
    state = await get_state(db, session_id)
    if state is None or not state.answer:
        return None
    stored = dict(state.answer)
    state.answer = None
    await db.commit()
    return stored

"""AI araştırma oturumları — başlat, izle, takip sorusu, iptal."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from showis.db.models import Case, PoolItem, ResearchMessage, ResearchSession, Setting
from showis.db.session import get_db
from showis.services import research_state
from showis.services.ai_providers import active_provider, allowed_models, get_provider
from showis.services.audit import record_request_audit
from showis.services.pool import ensure_case_pool
from showis.services.research import (
    DEPTH_PROFILES,
    MAX_STEPS_AGENT,
    MAX_STEPS_RESEARCH,
    cancel_research_session,
    research_broadcaster,
    research_event_stream,
    run_research_session,
    session_to_dict,
)
from showis.services.research_interactive import emit, get_control
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["research"])

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
#: Çalışan görev (veya kullanıcıyı bekleyen) oturumlar; vaka başına yalnız biri olabilir.
ACTIVE_STATUSES = ("queued", "running", "awaiting_user")
#: SSE tek seferlik yanıt verilen durumlar (interrupted: devam ettirilebilir).
STREAM_CLOSED_STATUSES = TERMINAL_STATUSES | {"interrupted"}


class ResearchStart(BaseModel):
    question: str = Field(min_length=10, max_length=4000)
    mode: Literal["research", "agent"] = "research"
    depth: Literal["quick", "standard", "deep"] = "deep"
    title: str | None = Field(default=None, max_length=256)
    provider_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)


class ResearchFollowUp(BaseModel):
    question: str = Field(min_length=2, max_length=4000)


class ResearchAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=2000)


async def _require_case(db: AsyncSession, case_id: str) -> Case:
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


async def _require_session(
    db: AsyncSession, case_id: str, session_id: str
) -> ResearchSession:
    session = (
        await db.execute(
            select(ResearchSession).where(
                ResearchSession.id == session_id, ResearchSession.case_id == case_id
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Research session not found")
    return session


async def _active_session(db: AsyncSession, case_id: str) -> ResearchSession | None:
    return (
        await db.execute(
            select(ResearchSession).where(
                ResearchSession.case_id == case_id,
                ResearchSession.status.in_(ACTIVE_STATUSES),
            )
        )
    ).scalars().first()


@router.get("/{case_id}/research/meta")
async def research_meta(case_id: str, db: AsyncSession = Depends(get_db)):
    await _require_case(db, case_id)
    provider = await active_provider(db)
    active: dict[str, Any] | None = None
    if provider is not None:
        active = {
            "id": provider.id,
            "name": provider.name,
            "model": (provider.config or {}).get("model"),
            "models": allowed_models(provider),
            "preset": provider.preset,
        }
    else:
        # Geriye dönük yol: eski tek `llm` ayarı veya ortam değişkeni.
        legacy = (
            await db.execute(
                select(Setting).where(
                    Setting.provider == "llm",
                    Setting.enabled.is_(True),
                    Setting.secret_encrypted.is_not(None),
                )
            )
        ).scalar_one_or_none()
        env_key = bool(
            os.getenv("OPENCODE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("LLM_API_KEY")
        )
        if legacy is not None or env_key:
            active = {
                "id": "legacy-llm",
                "name": "Eski LLM ayarı",
                "model": str((legacy.config or {}).get("model") or "")
                if legacy is not None
                else os.getenv("LLM_MODEL") or "minimax-m3.1-flash",
                "models": [],
                "preset": "legacy",
            }
    return {
        "active_provider": active,
        "limits": {
            "research": MAX_STEPS_RESEARCH,
            "agent": MAX_STEPS_AGENT,
        },
        "depth_profiles": {
            key: {
                "label": profile.label,
                "max_searches": profile.max_searches,
                "max_pages": profile.max_pages,
                "max_waves": profile.max_waves,
                "wall_seconds": profile.wall_seconds,
                "min_findings": profile.min_findings,
            }
            for key, profile in DEPTH_PROFILES.items()
        },
    }


@router.get("/{case_id}/research")
async def list_research_sessions(
    case_id: str,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    limit = max(1, min(100, limit))
    offset = max(0, min(10000, offset))
    total = (
        await db.execute(
            select(func.count())
            .select_from(ResearchSession)
            .where(ResearchSession.case_id == case_id)
        )
    ).scalar_one()
    sessions = (
        (
            await db.execute(
                select(ResearchSession)
                .where(ResearchSession.case_id == case_id)
                .order_by(ResearchSession.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [session_to_dict(session, summary=True) for session in sessions],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post("/{case_id}/research", status_code=201)
async def start_research(
    case_id: str,
    data: ResearchStart,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    await _require_case(db, case_id)
    # Eski vakalarda havuz tembel oluşturulur; ajan boş bağlamla başlamasın.
    await ensure_case_pool(db, case_id)
    if await _active_session(db, case_id) is not None:
        raise HTTPException(
            status_code=409, detail="Bu vakada çalışan bir araştırma oturumu var"
        )
    if data.provider_id:
        provider = await get_provider(db, data.provider_id)
        if provider is None or not provider.enabled:
            raise HTTPException(status_code=404, detail="AI provider not found")
        if not provider.secret_encrypted:
            raise HTTPException(
                status_code=422, detail="Seçili AI sağlayıcısının API anahtarı yok"
            )
    else:
        provider = await active_provider(db)
        if provider is not None and not provider.secret_encrypted:
            raise HTTPException(
                status_code=422, detail="Aktif AI sağlayıcısının API anahtarı yok"
            )
        if provider is None:
            legacy = (
                await db.execute(
                    select(Setting).where(
                        Setting.provider == "llm",
                        Setting.enabled.is_(True),
                        Setting.secret_encrypted.is_not(None),
                    )
                )
            ).scalar_one_or_none()
            env_key = bool(
                os.getenv("OPENCODE_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("LLM_API_KEY")
            )
            if legacy is None and not env_key:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "AI sağlayıcısı yapılandırılmamış: Ayarlar > LLM "
                        "bölümünde bir profil ekleyip aktifleştirin."
                    ),
                )

    if data.model and provider is not None:
        allowed = allowed_models(provider)
        if allowed and data.model not in allowed:
            raise HTTPException(
                status_code=422,
                detail="Seçilen model bu sağlayıcının izinli modellerinde yok",
            )

    actor = getattr(request.state, "actor", None)
    username = getattr(actor, "username", None) or "operator"
    title = " ".join((data.title or data.question).split())[:120]
    session = ResearchSession(
        case_id=case_id,
        title=title,
        question=data.question,
        mode=data.mode,
        provider_id=provider.id if provider else None,
        status="queued",
        created_by=username,
        stats={
            "depth": data.depth,
            **({"llm_model": data.model} if data.model else {}),
        },
    )
    db.add(session)
    try:
        await db.flush()
        db.add(
            ResearchMessage(
                session_id=session.id,
                role="user",
                content=data.question,
            )
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Bu vakada çalışan bir araştırma oturumu var"
        ) from None
    await db.refresh(session)
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.create",
        case_id=case_id,
        target=f"ResearchSession: {session.id}",
        details={
            "mode": session.mode,
            "depth": data.depth,
            "provider_id": session.provider_id,
            "question_length": len(session.question),
        },
    )
    background_tasks.add_task(run_research_session, session.id)
    return session_to_dict(session)


@router.get("/{case_id}/research/{session_id}")
async def get_research_session(
    case_id: str, session_id: str, db: AsyncSession = Depends(get_db)
):
    session = await _require_session(db, case_id, session_id)
    messages = (
        (
            await db.execute(
                select(ResearchMessage)
                .where(ResearchMessage.session_id == session.id)
                .order_by(ResearchMessage.created_at.asc(), ResearchMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    stored = await research_state.get_state(db, session.id)
    return session_to_dict(
        session,
        messages=messages,
        stored_question=research_state.pending_question_dict(stored),
    )


@router.post("/{case_id}/research/{session_id}/messages", status_code=202)
async def add_research_message(
    case_id: str,
    session_id: str,
    data: ResearchFollowUp,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    session = await _require_session(db, case_id, session_id)
    if session.status in ACTIVE_STATUSES:
        return await _steer_running_session(db, request, session, data.question, background_tasks)
    if await _active_session(db, case_id) is not None:
        raise HTTPException(
            status_code=409, detail="Bu vakada çalışan başka bir araştırma oturumu var"
        )
    db.add(
        ResearchMessage(
            session_id=session.id,
            role="user",
            content=data.question,
        )
    )
    session.status = "queued"
    session.error = None
    session.started_at = None
    session.finished_at = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Bu vakada çalışan başka bir araştırma oturumu var"
        ) from None
    await db.refresh(session)
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.follow_up",
        case_id=case_id,
        target=f"ResearchSession: {session.id}",
        details={"question_length": len(data.question)},
    )
    background_tasks.add_task(run_research_session, session.id)
    return session_to_dict(session)


async def _answer_offline(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    session: ResearchSession,
    question_id: str | None,
    text: str,
) -> bool:
    """API yeniden başlamış: DB'deki soruyu yanıtlar ve oturumu kuyruğa alır."""
    if session.status != "awaiting_user" or get_control(session.id) is not None:
        return False
    stored = await research_state.store_offline_answer(db, session.id, question_id, text)
    if stored is None:
        return False
    db.add(
        ResearchMessage(
            session_id=session.id,
            role="user",
            tool_name="answer",
            content=stored["answer"],
            tool_args={"question_id": stored["question_id"]},
        )
    )
    session.status = "queued"
    session.error = None
    await db.commit()
    background_tasks.add_task(run_research_session, session.id)
    return True


async def _steer_running_session(
    db: AsyncSession,
    request: Request,
    session: ResearchSession,
    text: str,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Çalışan oturuma mesaj: bekleyen soruya yanıt ya da yönlendirme kuyruğu (409 yok)."""
    control = get_control(session.id)
    if control is not None and control.pending is not None:
        # Ajan soru sormuş ve bekliyor: sohbetten gelen metin yanıt sayılır.
        if control.answer(control.pending.id, text):
            return {"id": session.id, "status": "running", "queued": False, "answered": True}
    if control is None and await _answer_offline(db, background_tasks, session, None, text):
        return {"id": session.id, "status": "queued", "queued": False, "answered": True}
    queued = control.push_steer(text) if control is not None else False
    await research_state.append_steer(db, session.id, text)
    db.add(
        ResearchMessage(
            session_id=session.id,
            role="user",
            tool_name="steer",
            content=text,
            tool_args={"queued": queued},
        )
    )
    await db.commit()
    await emit(
        session.id,
        "steer",
        {"session_id": session.id, "text": text[:600], "queued": queued},
    )
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.steer",
        case_id=session.case_id,
        target=f"ResearchSession: {session.id}",
        details={"text_length": len(text), "queued": queued},
    )
    return {"id": session.id, "status": session.status, "queued": True, "live": queued}


@router.post("/{case_id}/research/{session_id}/interrupt")
async def interrupt_research(
    case_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Çalışan araştırmayı bir sonraki adım sınırında durdurur; `resume` ile sürer."""
    session = await _require_session(db, case_id, session_id)
    if session.status not in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Oturum çalışmıyor")
    control = get_control(session.id)
    if control is not None:
        await research_state.set_interrupt(db, session.id, True)
        control.request_interrupt()
        status_text = "interrupting"
    else:
        # Süreç yeniden başlamış/çalışan görev yok: durumu doğrudan kapat.
        session.status = "interrupted"
        await db.commit()
        status_text = "interrupted"
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.interrupt",
        case_id=case_id,
        target=f"ResearchSession: {session.id}",
        details={"live": control is not None},
    )
    return {"id": session.id, "status": status_text}


@router.post("/{case_id}/research/{session_id}/resume", status_code=202)
async def resume_research(
    case_id: str,
    session_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    session = await _require_session(db, case_id, session_id)
    if session.status != "interrupted":
        raise HTTPException(status_code=409, detail="Yalnız durdurulmuş oturum sürdürülebilir")
    if await _active_session(db, case_id) is not None:
        raise HTTPException(
            status_code=409, detail="Bu vakada çalışan başka bir araştırma oturumu var"
        )
    session.status = "queued"
    session.error = None
    session.finished_at = None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Bu vakada çalışan başka bir araştırma oturumu var"
        ) from None
    await db.refresh(session)
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.resume",
        case_id=case_id,
        target=f"ResearchSession: {session.id}",
        details={},
    )
    background_tasks.add_task(run_research_session, session.id)
    return session_to_dict(session)


@router.post("/{case_id}/research/{session_id}/answer")
async def answer_research_question(
    case_id: str,
    session_id: str,
    data: ResearchAnswer,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Ajanın `ask_user` sorusunu yanıtlar; oturum kaldığı yerden sürer.

    API yeniden başlamışsa soru DB'den okunur ve oturum yanıtla yeniden kuyruğa alınır.
    """
    session = await _require_session(db, case_id, session_id)
    control = get_control(session.id)
    if control is not None:
        if not control.answer(data.question_id, data.answer):
            raise HTTPException(status_code=409, detail="Bekleyen soru yok")
    elif not await _answer_offline(
        db, background_tasks, session, data.question_id, data.answer
    ):
        raise HTTPException(status_code=409, detail="Bekleyen soru yok")
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.answer",
        case_id=case_id,
        target=f"ResearchSession: {session.id}",
        details={"answer_length": len(data.answer)},
    )
    return {"id": session.id, "status": "running", "answered": True}


@router.get("/{case_id}/research/{session_id}/proposals")
async def research_proposals(
    case_id: str, session_id: str, db: AsyncSession = Depends(get_db)
):
    """Bu oturumun onay bekleyen havuz önerileri (satır içi onay kartları)."""
    await _require_session(db, case_id, session_id)
    rows = (
        (
            await db.execute(
                select(PoolItem)
                .where(
                    PoolItem.case_id == case_id,
                    PoolItem.source == "ai",
                    PoolItem.status.in_(("proposed", "candidate")),
                )
                .order_by(PoolItem.created_at.desc())
                .limit(300)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "item_id": row.id,
            "kind": row.kind,
            "label": row.label,
            "value": str(row.value or "")[:600],
            "status": row.status,
            "confidence": float(row.confidence or 0.0),
            "source_url": (row.provenance or {}).get("url"),
        }
        for row in rows
        if (row.provenance or {}).get("session_id") == session_id
    ]
    return {"items": items, "total": len(items)}


@router.post("/{case_id}/research/{session_id}/cancel")
async def cancel_research(
    case_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session = await _require_session(db, case_id, session_id)
    if session.status in TERMINAL_STATUSES:
        return {"id": session.id, "status": session.status}
    await cancel_research_session(db, session)
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.cancel",
        case_id=case_id,
        target=f"ResearchSession: {session.id}",
        details={},
    )
    return {"id": session.id, "status": "cancelled"}


@router.delete("/{case_id}/research/{session_id}", status_code=204)
async def delete_research_session(
    case_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    session = await _require_session(db, case_id, session_id)
    if session.status in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Çalışan oturum silinemez")
    await db.delete(session)
    await db.commit()
    await record_request_audit(
        db=db,
        request=request,
        action="research.session.delete",
        case_id=case_id,
        target=f"ResearchSession: {session_id}",
        details={},
    )
    return None


@router.get("/{case_id}/research/{session_id}/events")
async def research_events(
    case_id: str, session_id: str, db: AsyncSession = Depends(get_db)
):
    session = await _require_session(db, case_id, session_id)
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

    if session.status in STREAM_CLOSED_STATUSES:
        event = (
            "research.completed"
            if session.status == "completed"
            else f"research.{session.status}"
        )
        payload = {
            "session_id": session.id,
            "status": session.status,
            "final_answer": session.final_answer,
            "citations": session.citations or [],
            "stats": session.stats or {},
            "error": session.error,
        }

        async def one_shot():
            yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(one_shot(), media_type="text/event-stream", headers=headers)

    # Aynı oturuma sonsuz sayıda abone belleği şişirmesin (tarama SSE'siyle aynı sınır).
    if research_broadcaster.subscriber_count(session.id) >= 3:
        raise HTTPException(status_code=429, detail="Bu araştırma için çok fazla canlı bağlantı")
    return StreamingResponse(
        research_event_stream(session.id), media_type="text/event-stream", headers=headers
    )

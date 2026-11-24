"""AI araştırma oturumları — başlat, izle, takip sorusu, iptal."""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from showis.db.models import Case, ResearchMessage, ResearchSession, Setting
from showis.db.session import get_db
from showis.services.ai_providers import active_provider, allowed_models, get_provider
from showis.services.audit import record_request_audit
from showis.services.pool import ensure_case_pool
from showis.services.research import (
    MAX_STEPS_AGENT,
    MAX_STEPS_RESEARCH,
    cancel_research_session,
    research_broadcaster,
    research_event_stream,
    run_research_session,
    session_to_dict,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["research"])

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ResearchStart(BaseModel):
    question: str = Field(min_length=10, max_length=4000)
    mode: Literal["research", "agent"] = "research"
    title: str | None = Field(default=None, max_length=256)
    provider_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=128)


class ResearchFollowUp(BaseModel):
    question: str = Field(min_length=2, max_length=4000)


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
                ResearchSession.status.in_(("queued", "running")),
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
        stats={"llm_model": data.model} if data.model else {},
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
    return session_to_dict(session, messages=messages)


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
    if session.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Araştırma oturumu çalışıyor")
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
    if session.status in {"queued", "running"}:
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

    if session.status in TERMINAL_STATUSES:
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

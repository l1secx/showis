"""Kayıtlı izleme uçları: uyarıları listele, okundu işaretle, izlemeyi elle çalıştır."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from showis.db.models import WatchAlert
from showis.db.session import get_db
from showis.services.audit import record_request_audit_safe
from showis.services.watch import run_watch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cases", tags=["watch"])

#: Elle izleme pahalıdır (dış sağlayıcılar + zincir). Aynı vakada art arda
#: çağrı sağlayıcıları tüketmesin diye kısa bir bekleme uygulanır.
_MANUAL_COOLDOWN_SECONDS = float(os.getenv("SHOWIS_WATCH_MANUAL_COOLDOWN", "60"))
_last_manual_run: dict[str, float] = {}


from .common import require_case as _require_case  # noqa: E402


def _alert_dict(alert: WatchAlert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "case_id": alert.case_id,
        "kind": alert.kind,
        "item_id": alert.item_id,
        "title": alert.title,
        "detail": alert.detail or {},
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "seen_at": alert.seen_at.isoformat() if alert.seen_at else None,
    }


@router.get("/{case_id}/watch/alerts")
async def list_alerts(
    case_id: str,
    unseen: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_case(db, case_id)
    stmt = select(WatchAlert).where(WatchAlert.case_id == case_id)
    if unseen:
        stmt = stmt.where(WatchAlert.seen_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(WatchAlert.created_at.desc()).limit(limit))).scalars().all()
    )
    return {"items": [_alert_dict(row) for row in rows], "total": int(total)}


@router.post("/{case_id}/watch/alerts/{alert_id}/seen")
async def mark_seen(
    case_id: str, alert_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    alert = (
        await db.execute(
            select(WatchAlert).where(WatchAlert.id == alert_id, WatchAlert.case_id == case_id)
        )
    ).scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="Uyarı bulunamadı")
    if alert.seen_at is None:
        alert.seen_at = datetime.now(timezone.utc)
        await db.commit()
    return _alert_dict(alert)


@router.post("/{case_id}/watch/run")
async def run(case_id: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    now = time.monotonic()
    last = _last_manual_run.get(case_id)
    if last is not None and now - last < _MANUAL_COOLDOWN_SECONDS:
        retry_after = max(1, int(_MANUAL_COOLDOWN_SECONDS - (now - last)))
        raise HTTPException(
            status_code=429,
            detail="İzleme yakın zamanda çalıştı; lütfen bekleyin",
            headers={"Retry-After": str(retry_after)},
        )
    case = await _require_case(db, case_id)
    _last_manual_run[case_id] = now
    try:
        result = await run_watch(db, case)
    except Exception:
        # Başarısız çalıştırma bekleme kilidi bırakmasın.
        _last_manual_run.pop(case_id, None)
        raise
    await record_request_audit_safe(
        db=db,
        request=request,
        action="watch.run",
        case_id=case_id,
        target=f"Case: {case_id}",
        details=result,
    )
    return result

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from showis.db.models import Scan
from showis.db.session import get_db
from showis.services.audit import record_request_audit
from showis.services.orchestrator import event_broadcaster
from showis.services.scanner import ScannerService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger("showis.api.scans")

router = APIRouter(prefix="/scans", tags=["scans"])

#: Ayni tarama icin es zamanli SSE baglanti siniri (SEC-16: bellek carpani)
_MAX_SSE_CONNECTIONS = 3
#: Heartbeat araligi (sn) — proxy/tarayici bos baglantiyi kesmesin
_SSE_HEARTBEAT_SECONDS = 20.0
#: Tek bir SSE akisinin azami omru (sn); `SHOWIS_SSE_MAX_SECONDS` ile override
_DEFAULT_SSE_MAX_SECONDS = 900.0

#: scan_id -> acik baglanti sayisi (sinif duzeyi, surec ici)
_sse_connections: dict[str, int] = {}

_FINAL_STATUSES = {"completed", "failed", "cancelled"}


def _revoke_celery_task(task_id: str) -> None:
    """Celery yolunda calisan taramayi iptal eder (broker yoksa sessiz log)."""
    try:
        from showis.services.worker import celery_app

        celery_app.control.revoke(task_id, terminate=True)
    except Exception as exc:  # broker/worker yok: sessizce logla
        logger.warning("Celery revoke basarisiz (%s): %s", task_id, exc)


def _sse_max_seconds() -> float:
    """SSE akis omru (sn). Testler `SHOWIS_SSE_MAX_SECONDS` ile kisaltir."""
    raw = os.getenv("SHOWIS_SSE_MAX_SECONDS")
    if not raw:
        return _DEFAULT_SSE_MAX_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Gecersiz SHOWIS_SSE_MAX_SECONDS=%r, varsayilan kullanilir", raw)
        return _DEFAULT_SSE_MAX_SECONDS
    return max(0.0, value)


def _sse_payload(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _scan_event_stream(scan_id: str, initial_status: str) -> AsyncIterator[str]:
    """Sinirli omurlu, heartbeat'li SSE akisi (SEC-16).

    * Ilk baglanti sayaaci burada tutulur, `finally` ile **her durumda** duser.
    * Tarama zaten bittiyse tek bir terminal olay gonderip hemen kapanir
      (sonsuz bekleme yok).
    * `SHOWIS_SSE_MAX_SECONDS` dolunca `: bye` yorumu + kapanis.
    """
    _sse_connections[scan_id] = _sse_connections.get(scan_id, 0) + 1
    queue: asyncio.Queue[dict[str, Any]] | None = None
    try:
        if initial_status in _FINAL_STATUSES:
            yield _sse_payload(f"scan.{initial_status}", {"status": initial_status})
            return

        queue = event_broadcaster.subscribe(scan_id)
        # Ilk bayti hemen gonder: proxy/tarayici baglantiyi acik saysin, testler
        # heartbeat beklemesin (SSE yorumlari istemciye olay olarak gitmez).
        yield ": connected\n\n"
        deadline = asyncio.get_running_loop().time() + _sse_max_seconds()

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                yield ": bye\n\n"
                return
            timeout = min(_SSE_HEARTBEAT_SECONDS, remaining)
            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                if asyncio.get_running_loop().time() >= deadline:
                    yield ": bye\n\n"
                    return
                yield ": ping\n\n"
                continue

            yield _sse_payload(item["type"], item["data"])
            if item["type"] in ("scan.completed", "scan.failed", "scan.cancelled"):
                return
    finally:
        if queue is not None:
            event_broadcaster.unsubscribe(scan_id, queue)
        remaining_connections = _sse_connections.get(scan_id, 1) - 1
        if remaining_connections > 0:
            _sse_connections[scan_id] = remaining_connections
        else:
            _sse_connections.pop(scan_id, None)


@router.get("/{scan_id}")
async def get_scan_status(scan_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Scan).options(selectinload(Scan.tasks)).where(Scan.id == scan_id))
    scan = res.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    return {
        "id": scan.id,
        "case_id": scan.case_id,
        "status": scan.status,
        "mode": scan.mode,
        "stats": scan.stats,
        "tasks_count": len(scan.tasks),
        "tasks": [
            {
                "id": t.id,
                "connector": t.connector,
                "platform": t.platform,
                "kind": t.kind,
                "status": t.status,
                "attempts": t.attempts,
                "observation_count": t.observation_count,
                "error": t.error,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            }
            for t in scan.tasks
        ],
        "started_at": scan.started_at.isoformat() if scan.started_at else None,
        "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
    }


@router.get("/{scan_id}/events")
async def stream_scan_events(scan_id: str, db: AsyncSession = Depends(get_db)):
    """Canli SSE olay akisi (SEC-16: kimlik/var olma kontrolu + baglanti siniri)."""
    scan = (
        await db.execute(select(Scan.id, Scan.status).where(Scan.id == scan_id))
    ).first()
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    if _sse_connections.get(scan_id, 0) >= _MAX_SSE_CONNECTIONS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Bu tarama icin en fazla {_MAX_SSE_CONNECTIONS} canli baglanti acilabilir"
            ),
        )

    return StreamingResponse(
        _scan_event_stream(scan_id, str(scan.status)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{scan_id}/cancel")
async def cancel_scan(
    scan_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Taramayi iptal eder: DB durumu + surec ici kanal + (varsa) Celery revoke.

    `ScannerService` calisan dongusu kanali gorunce yeni gorev dispatch etmez,
    ucustaki gorevleri `CancelledError` ile iptal eder ve final durumu kosullu
    UPDATE ile yazar — boylece `cancelled` hicbir zaman `completed` ile ezilmez.
    """
    res = await db.execute(select(Scan).where(Scan.id == scan_id))
    scan = res.scalar_one_or_none()
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")

    was_cancelled = scan.status == "cancelled"
    scan.status = "cancelled"
    await db.commit()

    # 1) Surec ici iptal kanali (API ile ayni event loop'taki calisan tarama)
    ScannerService.cancel(scan.id)

    if was_cancelled:
        # Idempotent: ikinci cagri yeni audit/SSE uretmez
        return {"status": "cancelled", "scan_id": scan.id}

    # 2) Celery yolu: tarama baska bir surecte kosuyorsa gorevi geri cagir
    celery_task_id = (scan.stats or {}).get("celery_task_id")
    if celery_task_id:
        _revoke_celery_task(str(celery_task_id))

    await event_broadcaster.broadcast(scan.id, "scan.cancelled", {"status": "cancelled"})
    await record_request_audit(
        db=db,
        request=request,
        action="scan.cancel",
        case_id=scan.case_id,
        target=f"Scan: {scan.id}",
        details={"cancelled_at": datetime.now(timezone.utc).isoformat()},
    )

    return {"status": "cancelled", "scan_id": scan.id}

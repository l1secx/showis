import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
from starlette.requests import ClientDisconnect

logger = logging.getLogger("showis.api.scans")

router = APIRouter(prefix="/scans", tags=["scans"])

#: Ayni tarama icin es zamanli SSE baglanti siniri (SEC-16: bellek carpani)
_MAX_SSE_CONNECTIONS = 3
#: Heartbeat araligi (sn) — proxy/tarayici bos baglantiyi kesmesin
_SSE_HEARTBEAT_SECONDS = 20.0
#: Tek bir SSE akisinin azami omru (sn); `SHOWIS_SSE_MAX_SECONDS` ile override
_DEFAULT_SSE_MAX_SECONDS = 900.0
#: T-354: bu sureden uzun sure yenilenmeyen kira "terk edilmis" sayilir ve dusurulur
_SSE_LEASE_TTL_SECONDS = 90.0
#: 429 yanitinda istemciye onerilen bekleme (sn)
_SSE_RETRY_AFTER_SECONDS = 5

#: scan_id -> acik baglanti sayisi (turetilmis; geriye uyum + gozlemlenebilirlik)
_sse_connections: dict[str, int] = {}

_FINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class SseLease:
    """Bir SSE baglantisinin slot kirasi (T-354).

    `renewed_at` her ping/olay yaziminda guncellenir. Proxy baglantiyi
    kapatmadan biraktiginda (Next `fetch` iptali upstream'e ulasmadiginda)
    `finally` hic calismaz; bu yuzden:
    * `renewed_at` TTL'i (90 sn) dolan kiralar yeni baglanti isteginde dusurulur,
    * dongude her turda `request.is_disconnected()` kontrol edilir,
    * yazim hatasi (`ClientDisconnect`/`OSError`) slotu derhal birakir.
    """

    lease_id: str
    scan_id: str
    created_at: float
    renewed_at: float


#: scan_id -> {lease_id: SseLease} (surec ici)
_sse_leases: dict[str, dict[str, SseLease]] = {}


def _now() -> float:
    """Kira zamani (testlerde monkeypatch edilir)."""
    return time.monotonic()


def _sync_connection_counter(scan_id: str) -> None:
    """`_sse_connections` sayacini kira tablosundan turetir (tek kaynak)."""
    leases = _sse_leases.get(scan_id)
    if leases:
        _sse_connections[scan_id] = len(leases)
    else:
        _sse_connections.pop(scan_id, None)


def reap_stale_sse_leases(scan_id: str, *, now: float | None = None) -> int:
    """TTL'i dolmus (terk edilmis) kiralari dusurur; dusurulen sayiyi doner.

    Zamanlayici yok: temizlik yeni baglanti isteginde (ve gaita) yapilir.
    """
    leases = _sse_leases.get(scan_id)
    if not leases:
        return 0
    current = _now() if now is None else now
    stale = [
        lease_id
        for lease_id, lease in leases.items()
        if current - lease.renewed_at > _SSE_LEASE_TTL_SECONDS
    ]
    for lease_id in stale:
        leases.pop(lease_id, None)
    if stale:
        logger.info(
            "SSE: %d terk edilmis kira dusuruldu (scan=%s, ttl=%.0fs)",
            len(stale),
            scan_id,
            _SSE_LEASE_TTL_SECONDS,
        )
    _sync_connection_counter(scan_id)
    return len(stale)


def acquire_sse_lease(scan_id: str) -> SseLease:
    """Slot kirasi alir (cagiran once `reap_stale_sse_leases` + limit kontrolu yapar)."""
    current = _now()
    lease = SseLease(
        lease_id=uuid.uuid4().hex,
        scan_id=scan_id,
        created_at=current,
        renewed_at=current,
    )
    _sse_leases.setdefault(scan_id, {})[lease.lease_id] = lease
    _sync_connection_counter(scan_id)
    return lease


def renew_sse_lease(lease: SseLease, *, now: float | None = None) -> None:
    lease.renewed_at = _now() if now is None else now


def release_sse_lease(lease: SseLease) -> None:
    """Kira slotunu birakir (idempotent)."""
    leases = _sse_leases.get(lease.scan_id)
    if leases and leases.pop(lease.lease_id, None) is not None:
        if not leases:
            _sse_leases.pop(lease.scan_id, None)
        _sync_connection_counter(lease.scan_id)


def reset_sse_leases() -> None:
    """Tum kiralari temizler (testler / servis yeniden baslatma)."""
    _sse_leases.clear()
    _sse_connections.clear()


def active_sse_leases(scan_id: str) -> int:
    return len(_sse_leases.get(scan_id, {}))


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


async def _client_disconnected(request: Request | None) -> bool:
    """Istemci koptu mu? (periyodik kontrol; bloklamaz)"""
    if request is None:
        return False
    try:
        return bool(await request.is_disconnected())
    except Exception:  # pragma: no cover - disconnect kontrolu akisi bozmasin
        return False


async def _scan_event_stream(
    scan_id: str,
    initial_status: str,
    request: Request | None = None,
    lease: SseLease | None = None,
) -> AsyncIterator[str]:
    """Sinirli omurlu, heartbeat'li SSE akisi (SEC-16 + T-354 kira).

    Slot yonetimi:
    * Kira endpoint'te alinir (429 kontrolu ile atomik), burada **her durumda**
      `finally` ile birakilir.
    * Her ping/olay yaziminda `renewed_at` guncellenir (proxy kopuklugunda TTL
      reap devreye girer).
    * Her turda `request.is_disconnected()` kontrol edilir; kopmussa cikilir.
    * Yazim sirasinda `ClientDisconnect`/`OSError` slotu derhal birakir.
    * Tarama bittiyse tek terminal olay gonderip kapanir; `SHOWIS_SSE_MAX_SECONDS`
      dolunca `: bye` yorumu ile kapanir.
    """
    lease = lease or acquire_sse_lease(scan_id)
    queue: asyncio.Queue[dict[str, Any]] | None = None
    try:
        if initial_status in _FINAL_STATUSES:
            yield _sse_payload(f"scan.{initial_status}", {"status": initial_status})
            return

        queue = event_broadcaster.subscribe(scan_id)
        # Ilk bayti hemen gonder: proxy/tarayici baglantiyi acik saysin, testler
        # heartbeat beklemesin (SSE yorumlari istemciye olay olarak gitmez).
        yield ": connected\n\n"
        deadline = _now() + _sse_max_seconds()

        while True:
            if await _client_disconnected(request):
                logger.info("SSE: istemci koptu, akis kapatiliyor (scan=%s)", scan_id)
                return
            remaining = deadline - _now()
            if remaining <= 0:
                yield ": bye\n\n"
                return
            timeout = min(_SSE_HEARTBEAT_SECONDS, remaining)
            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                renew_sse_lease(lease)
                if _now() >= deadline:
                    yield ": bye\n\n"
                    return
                yield ": ping\n\n"
                continue

            renew_sse_lease(lease)
            yield _sse_payload(item["type"], item["data"])
            if item["type"] in ("scan.completed", "scan.failed", "scan.cancelled"):
                return
    except (ClientDisconnect, OSError):
        # Yazim/akis sirasinda kopan baglanti: kira `finally`de birakilir
        logger.info("SSE: yazim hatasi, akis kapatiliyor (scan=%s)", scan_id)
        return
    finally:
        release_sse_lease(lease)
        if queue is not None:
            dropped = getattr(queue, "dropped", 0)
            if dropped:
                # Yavas istemci: olay kaybi sessiz kalmasin (SEC-16)
                logger.warning(
                    "SSE akisi kapandi (scan=%s): %d olay dusuruldu (kapasite=%d)",
                    scan_id,
                    dropped,
                    getattr(queue, "maxsize", 0),
                )
            event_broadcaster.unsubscribe(scan_id, queue)


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
async def stream_scan_events(
    scan_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Canli SSE olay akisi (SEC-16 + T-354 kira/slot yonetimi).

    Slot yonetimi:
    * Terk edilmis (90 sn yenilenmeyen) kiralar **yeni baglanti isteginde**
      dusurulur — ayri zamanlayici yok.
    * `scan_id` basina en fazla `_MAX_SSE_CONNECTIONS` (3) aktif kira; doluysa
      `429` + `Retry-After` (kac saniye sonra denenecegi `detail`de).
    * Kira burada alinir (kontrol ile ayni adimda), akis `finally`de birakir.
    """
    scan = (
        await db.execute(select(Scan.id, Scan.status).where(Scan.id == scan_id))
    ).first()
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")

    reap_stale_sse_leases(scan_id)
    active = active_sse_leases(scan_id)
    if active >= _MAX_SSE_CONNECTIONS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Bu tarama icin en fazla {_MAX_SSE_CONNECTIONS} canli baglanti "
                f"acilabilir; {_SSE_RETRY_AFTER_SECONDS} saniye sonra yeniden deneyin"
            ),
            headers={"Retry-After": str(_SSE_RETRY_AFTER_SECONDS)},
        )

    lease = acquire_sse_lease(scan_id)
    return StreamingResponse(
        _scan_event_stream(scan_id, str(scan.status), request=request, lease=lease),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # T-354: proxy/istemci tarama durumunu basliktan gorebilsin
            "X-Scan-Status": str(scan.status),
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

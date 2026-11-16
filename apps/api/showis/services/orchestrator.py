import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("showis.orchestrator")

#: Abone kuyrugu varsayilan kapasitesi (SEC-16): yavas istemcide bellek sinirsiz
#: buyumesin. `SHOWIS_SSE_QUEUE_MAXSIZE` ile degistirilebilir.
DEFAULT_QUEUE_MAXSIZE = 500


def queue_maxsize() -> int:
    raw = os.getenv("SHOWIS_SSE_QUEUE_MAXSIZE")
    if not raw:
        return DEFAULT_QUEUE_MAXSIZE
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        logger.warning("Gecersiz SHOWIS_SSE_QUEUE_MAXSIZE=%r, varsayilan kullanilir", raw)
        return DEFAULT_QUEUE_MAXSIZE


class ScanEventQueue(asyncio.Queue[dict[str, Any]]):
    """Sinirli olay kuyrugu: doldugunda **en eski** olay dusurulur.

    `asyncio.Queue` arayuzu korunur (`get`/`put_nowait`/`qsize`), bu yuzden
    mevcut tuketiciler degismeden calisir. Dusen olay sayisi `dropped` alaninda
    tutulur; `broadcast` bunu periyodik olarak loglar.
    """

    def __init__(self, maxsize: int = DEFAULT_QUEUE_MAXSIZE):
        super().__init__(maxsize=maxsize)
        self.dropped = 0

    def offer(self, item: dict[str, Any]) -> bool:
        """Olayi kuyruga koyar; dolduysa en eskiyi atar. Yeni olay girdiyse True."""
        try:
            self.put_nowait(item)
            return True
        except asyncio.QueueFull:
            pass

        # En eski olayi dusur (kuyruk bossa yaris durumu: tekrar dene)
        try:
            self.get_nowait()
            try:
                self.task_done()
            except ValueError:  # unfinished sayaç zaten 0 (join kullanilmiyor)
                pass
            self.dropped += 1
        except asyncio.QueueEmpty:  # pragma: no cover - tuketici arada bosaltti
            pass

        try:
            self.put_nowait(item)
            return True
        except asyncio.QueueFull:  # pragma: no cover - maxsize=1 + yaris
            self.dropped += 1
            return False


class ScanEventBroadcaster:
    """Scan bazlı SSE olay kuyruğu yönetimi."""

    def __init__(self, maxsize: int | None = None):
        self._queues: dict[str, list[ScanEventQueue]] = {}
        self._maxsize = maxsize

    # ------------------------------------------------------------------
    # Abonelik
    # ------------------------------------------------------------------

    def subscribe(self, scan_id: str, maxsize: int | None = None) -> ScanEventQueue:
        size = maxsize or self._maxsize or queue_maxsize()
        q = ScanEventQueue(maxsize=size)
        self._queues.setdefault(scan_id, []).append(q)
        return q

    def unsubscribe(self, scan_id: str, q: asyncio.Queue[Any]) -> None:
        queues = self._queues.get(scan_id)
        if queues and q in queues:
            queues.remove(q)
            if not queues:
                del self._queues[scan_id]

    # ------------------------------------------------------------------
    # Yayin
    # ------------------------------------------------------------------

    async def broadcast(self, scan_id: str, event_type: str, data: dict[str, Any]) -> None:
        payload = {
            "type": event_type,
            "scan_id": scan_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        queues = self._queues.get(scan_id, [])
        for q in list(queues):
            try:
                if isinstance(q, ScanEventQueue):
                    q.offer(payload)
                    # Seyrek log: her 50 dusen olayda bir uyari
                    if q.dropped and q.dropped % 50 == 0:
                        logger.warning(
                            "SSE kuyrugu dolu (scan=%s): %d olay dusuruldu (kapasite=%d)",
                            scan_id,
                            q.dropped,
                            q.maxsize,
                        )
                else:  # pragma: no cover - geriye uyum (duz asyncio.Queue)
                    q.put_nowait(payload)
            except asyncio.QueueFull:  # pragma: no cover
                logger.warning("SSE kuyrugu dolu (scan=%s), olay dusuruldu", scan_id)
            except Exception as e:
                logger.error("SSE broadcast hatası: %s", e)

    # ------------------------------------------------------------------
    # Gozlemlenebilirlik / testler
    # ------------------------------------------------------------------

    def subscriber_count(self, scan_id: str) -> int:
        return len(self._queues.get(scan_id, []))

    def dropped_events(self, scan_id: str) -> int:
        """Abonelere ait toplam dusen olay sayisi (istatistik/log icin)."""
        return sum(getattr(q, "dropped", 0) for q in self._queues.get(scan_id, []))


event_broadcaster = ScanEventBroadcaster()


async def sse_event_stream(scan_id: str) -> AsyncGenerator[str, None]:
    q = event_broadcaster.subscribe(scan_id)
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=20.0)
                yield f"event: {item['type']}\ndata: {json.dumps(item['data'])}\n\n"
                if item["type"] in ("scan.completed", "scan.failed", "scan.cancelled"):
                    break
            except asyncio.TimeoutError:
                # Keep-alive SSE comment
                yield ": ping\n\n"
    finally:
        event_broadcaster.unsubscribe(scan_id, q)

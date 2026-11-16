import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

logger = logging.getLogger("showis.orchestrator")


class ScanEventBroadcaster:
    """Scan bazlı SSE olay kuyruğu yönetimi."""

    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, scan_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if scan_id not in self._queues:
            self._queues[scan_id] = []
        self._queues[scan_id].append(q)
        return q

    def unsubscribe(self, scan_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        if scan_id in self._queues and q in self._queues[scan_id]:
            self._queues[scan_id].remove(q)
            if not self._queues[scan_id]:
                del self._queues[scan_id]

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
                await q.put(payload)
            except Exception as e:
                logger.error("SSE broadcast hatası: %s", e)


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

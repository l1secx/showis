import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("showis.orchestrator")

#: Abone kuyrugu varsayilan kapasitesi (SEC-16): yavas istemcide bellek sinirsiz
#: buyumesin. `SHOWIS_SSE_QUEUE_MAXSIZE` ile degistirilebilir.
DEFAULT_QUEUE_MAXSIZE = 500

#: Tarama olaylarini süreçler arası taşıyan Redis pub/sub kanalı (DEC-005).
#: Varsayılan: `REDIS_URL` tanımlıysa açık (Docker/Celery); testlerde kapalı.
REDIS_EVENTS_ENV = "SHOWIS_SCAN_EVENTS_REDIS"
_CHANNEL_PREFIX = "showis:scan-events:"
_redis_client: Any | None = None
_redis_client_lock = asyncio.Lock()
_redis_warn_at = 0.0


def redis_events_enabled() -> bool:
    """Tarama olayları Redis'e mi yayınlanacak? (test/dev'de varsayılan kapalı)"""
    raw = os.getenv(REDIS_EVENTS_ENV)
    if raw is not None and raw.strip():
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(os.getenv("REDIS_URL"))


def scan_event_channel(scan_id: str) -> str:
    return f"{_CHANNEL_PREFIX}{scan_id}"


async def _get_redis_client() -> Any | None:
    """Lazy Redis istemcisi; bağlantı kurulamazsa None (yerel yayın sürer)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    async with _redis_client_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            from redis.asyncio import Redis

            _redis_client = Redis.from_url(
                os.getenv("REDIS_URL", "redis://redis:6379/0"),
                decode_responses=True,
            )
        except Exception as exc:  # noqa: BLE001 - Redis yoksa SSE yerelde kalır
            logger.warning("Redis olay istemcisi kurulamadi: %s", type(exc).__name__)
            return None
    return _redis_client


def reset_scan_events_redis() -> None:
    """Testler için: lazy istemciyi ve uyarı sayacını sıfırlar."""
    global _redis_client, _redis_warn_at
    _redis_client = None
    _redis_warn_at = 0.0


async def publish_scan_event(payload: dict[str, Any]) -> None:
    """Olayı Redis kanalına yayınlar; hata yerel akışı bozmaz (log throttled)."""
    global _redis_warn_at
    client = await _get_redis_client()
    if client is None:
        return
    channel = scan_event_channel(str(payload.get("scan_id") or ""))
    try:
        await asyncio.wait_for(
            client.publish(channel, json.dumps(payload, ensure_ascii=False)),
            timeout=2.0,
        )
    except Exception as exc:  # noqa: BLE001 - publish best-effort
        now = time.monotonic()
        if now - _redis_warn_at >= 60:
            _redis_warn_at = now
            logger.warning(
                "SSE Redis publish basarisiz (scan=%s): %s",
                payload.get("scan_id"),
                type(exc).__name__,
            )


async def redis_event_feeder(scan_id: str, queue: "ScanEventQueue") -> None:
    """Redis kanalını dinleyip yerel SSE kuyruğuna aktarır (API süreci).

    Bağlantı kurulamazsa sessizce döner; SSE yerel kuyrukla (varsa) çalışır.
    """
    if not redis_events_enabled():
        return
    client = await _get_redis_client()
    if client is None:
        return
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(scan_event_channel(scan_id))
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else None
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                queue.offer(payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - abone hatası SSE'yi düşürmesin
        logger.warning(
            "SSE Redis aboneligi kapandi (scan=%s): %s", scan_id, type(exc).__name__
        )
    finally:
        try:
            await pubsub.unsubscribe(scan_event_channel(scan_id))
        except Exception:  # noqa: BLE001
            pass
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass


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

    def __init__(self, maxsize: int | None = None, *, remote: bool = True):
        self._queues: dict[str, list[ScanEventQueue]] = {}
        self._maxsize = maxsize
        #: True ise olaylar Redis pub/sub'e de yayınlanır (süreçler arası SSE).
        self.remote = remote

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
            "event_id": uuid.uuid4().hex,
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
        if self.remote and redis_events_enabled():
            await publish_scan_event(payload)

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

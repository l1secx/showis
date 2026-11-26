"""DEC-005: tarama SSE olayları Redis pub/sub ile süreçler arası taşınır."""

from __future__ import annotations

import json

import pytest
from showis.api.v1.scans import _already_seen
from showis.services import orchestrator


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, json.loads(payload)))
        return 1


@pytest.mark.asyncio
async def test_broadcast_publishes_remote_with_event_id(monkeypatch):
    fake = _FakeRedis()

    async def _fake_client():
        return fake

    monkeypatch.setattr(orchestrator, "redis_events_enabled", lambda: True)
    monkeypatch.setattr(orchestrator, "_get_redis_client", _fake_client)
    broadcaster = orchestrator.ScanEventBroadcaster(maxsize=10)
    queue = broadcaster.subscribe("scan-redis-1")

    await broadcaster.broadcast("scan-redis-1", "scan.status", {"status": "running"})

    local = queue.get_nowait()
    assert local["type"] == "scan.status"
    assert local["event_id"]
    assert len(fake.published) == 1
    channel, remote = fake.published[0]
    assert channel == orchestrator.scan_event_channel("scan-redis-1")
    # Yerel ve Redis kopyası aynı olay kimliğini taşır: istemci tekilleştirir.
    assert remote["event_id"] == local["event_id"]


@pytest.mark.asyncio
async def test_remote_disabled_does_not_publish(monkeypatch):
    fake = _FakeRedis()

    async def _fake_client():
        return fake

    monkeypatch.setattr(orchestrator, "redis_events_enabled", lambda: False)
    monkeypatch.setattr(orchestrator, "_get_redis_client", _fake_client)
    broadcaster = orchestrator.ScanEventBroadcaster(maxsize=10)
    broadcaster.subscribe("scan-redis-2")

    await broadcaster.broadcast("scan-redis-2", "scan.status", {"status": "running"})

    assert fake.published == []


def test_event_dedupe_by_id():
    seen: dict[str, None] = {}
    assert _already_seen(seen, "abc") is False
    assert _already_seen(seen, "abc") is True
    assert _already_seen(seen, "") is False

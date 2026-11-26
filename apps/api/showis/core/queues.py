"""Celery kuyruk adları — tek kaynak (API ve worker aynı env'i okur)."""

from __future__ import annotations

import os

SCAN_QUEUE_ENV = "SHOWIS_SCAN_QUEUE"
DEFAULT_SCAN_QUEUE = "osint"


def scan_queue() -> str:
    """Tarama/beat görevlerinin kuyruğu; boş/geçersiz env varsayılana düşer."""
    value = (os.getenv(SCAN_QUEUE_ENV) or "").strip()
    return value or DEFAULT_SCAN_QUEUE

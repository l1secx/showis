"""Celery kuyruk adları — tek kaynak (API ve worker aynı env'i okur)."""

from __future__ import annotations

import os

SCAN_QUEUE_ENV = "SHOWIS_SCAN_QUEUE"
DEFAULT_SCAN_QUEUE = "osint"


def scan_queue() -> str:
    """Tarama/beat görevlerinin kuyruğu; boş/geçersiz env varsayılana düşer."""
    value = (os.getenv(SCAN_QUEUE_ENV) or "").strip()
    return value or DEFAULT_SCAN_QUEUE


def celery_redis_url(url: str) -> str:
    """Celery broker/backend için Redis URL'si.

    Celery, TLS'li (`rediss://`) bağlantıda `ssl_cert_reqs` ister ve yoksa
    `ValueError` verir. Yönetilen Redis (Upstash) `rediss://` verir; sertifika
    doğrulaması açık (`required`) eklenir. Zaten belirtilmişse dokunulmaz.
    """
    if not url.startswith("rediss://") or "ssl_cert_reqs=" in url:
        return url
    return f"{url}{'&' if '?' in url else '?'}ssl_cert_reqs=required"

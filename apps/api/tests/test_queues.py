"""SHOWIS_SCAN_QUEUE tek kaynak: API dispatch ve worker/beat aynı adı kullanır."""

from showis.core.queues import DEFAULT_SCAN_QUEUE, scan_queue
from showis.services import worker


def test_scan_queue_default(monkeypatch):
    monkeypatch.delenv("SHOWIS_SCAN_QUEUE", raising=False)
    assert scan_queue() == DEFAULT_SCAN_QUEUE
    assert worker.celery_app.conf.task_default_queue == worker.SCAN_QUEUE


def test_scan_queue_env_override(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCAN_QUEUE", "osint-ozel")
    assert scan_queue() == "osint-ozel"

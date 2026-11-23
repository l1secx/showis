"""Tarama sonrası kanıt zenginleştirme — bounded screenshot/Wayback."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from showis.services import enrichment


class _Result:
    def scalars(self):
        return self

    def all(self):
        return []


class _Session:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False

    async def execute(self, *_args, **_kwargs):
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


class _Browser:
    def __init__(self, *, status: int = 200, png: bytes | None = b"\x89PNG"):
        self.status = status
        self.png = png

    async def render(self, _url: str):
        return SimpleNamespace(
            status_code=self.status,
            screenshot_b64=base64.b64encode(self.png).decode() if self.png else None,
            blocked=False,
        )


def _finding(url: str = "https://example.com/a", score: float = 0.9):
    return SimpleNamespace(
        id="f1",
        url=url,
        score=score,
        status="confirmed",
        evidence_ids=[],
    )


@pytest.mark.asyncio
async def test_screenshot_kaniti_eklenir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCREENSHOT_CAPTURE", "1")
    monkeypatch.setattr(enrichment, "BrowserClient", lambda *a, **k: _Browser())
    monkeypatch.setattr(enrichment, "guard_url", lambda _url: _url)
    db = _Session()

    added = await enrichment.capture_scan_evidence(
        db,
        SimpleNamespace(id="c1", retention_days=30),
        SimpleNamespace(id="s1", mode="O3"),
        [_finding()],
    )

    assert added == 1
    assert db.committed is True
    evidence = db.added[0]
    assert evidence.kind == "png"
    assert evidence.case_id == "c1"


@pytest.mark.asyncio
async def test_404_wayback_json_kullanir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCREENSHOT_CAPTURE", "1")
    monkeypatch.setattr(
        enrichment, "BrowserClient", lambda *a, **k: _Browser(status=404, png=None)
    )
    monkeypatch.setattr(enrichment, "guard_url", lambda _url: _url)

    async def _archive(_url: str):
        return b'[["timestamp"],["20240101000000"]]', "https://web.archive.org/x", 200

    monkeypatch.setattr(enrichment, "_archive_snapshot", _archive)
    db = _Session()

    added = await enrichment.capture_scan_evidence(
        db,
        SimpleNamespace(id="c1", retention_days=30),
        SimpleNamespace(id="s1", mode="O3"),
        [_finding()],
    )

    assert added == 1
    assert db.added[0].kind == "json"


@pytest.mark.asyncio
async def test_ssrf_hedefi_atlanir(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCREENSHOT_CAPTURE", "1")

    def _deny(_url: str):
        raise enrichment.SSRFViolationError("özel IP")

    monkeypatch.setattr(enrichment, "guard_url", _deny)
    db = _Session()

    added = await enrichment.capture_scan_evidence(
        db,
        SimpleNamespace(id="c1", retention_days=30),
        SimpleNamespace(id="s1", mode="O3"),
        [_finding(url="http://127.0.0.1/admin")],
    )

    assert added == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_kapatma_bayragi(monkeypatch):
    monkeypatch.setenv("SHOWIS_SCREENSHOT_CAPTURE", "0")
    db = _Session()

    added = await enrichment.capture_scan_evidence(
        db,
        SimpleNamespace(id="c1", retention_days=30),
        SimpleNamespace(id="s1", mode="O3"),
        [_finding()],
    )

    assert added == 0

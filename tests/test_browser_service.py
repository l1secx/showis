"""Browser servisi route guard (DEC-003 / P-001)."""

from __future__ import annotations

import pytest
from showis_engine import browser_service


class _Request:
    def __init__(self, url: str) -> None:
        self.url = url


class _Route:
    def __init__(self, url: str) -> None:
        self.request = _Request(url)
        self.aborted = False
        self.continued = False

    async def abort(self, reason: str = "") -> None:
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


@pytest.mark.asyncio
async def test_render_route_private_ip_engellenir():
    route = _Route("http://127.0.0.1:8080/admin")
    await browser_service._guard_route(route)
    assert route.aborted is True
    assert route.continued is False


@pytest.mark.asyncio
async def test_render_route_robots_engeli(monkeypatch):
    route = _Route("https://93.184.216.34/profil")

    async def _deny(url: str) -> bool:
        return False

    monkeypatch.setattr(browser_service._ROBOTS, "is_allowed", _deny)
    await browser_service._guard_route(route)
    assert route.aborted is True
    assert route.continued is False


@pytest.mark.asyncio
async def test_render_route_public_gecer(monkeypatch):
    route = _Route("https://93.184.216.34/profil")

    async def _allow(url: str) -> bool:
        return True

    monkeypatch.setattr(browser_service._ROBOTS, "is_allowed", _allow)
    await browser_service._guard_route(route)
    assert route.continued is True
    assert route.aborted is False


@pytest.mark.asyncio
async def test_render_route_http_disi_sema_gecer(monkeypatch):
    route = _Route("data:image/png;base64,AAAA")
    await browser_service._guard_route(route)
    assert route.continued is True

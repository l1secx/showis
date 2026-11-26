import pytest
import respx
from showis_engine.http import RobotsCache


@pytest.mark.asyncio
async def test_robots_cache_disallow():
    cache = RobotsCache()

    robots_content = """User-agent: *
Disallow: /api/
Disallow: /private/
Allow: /public/
"""
    with respx.mock:
        respx.get("https://example.com/robots.txt").respond(200, text=robots_content)

        assert await cache.is_allowed("https://example.com/public/profile") is True
        assert await cache.is_allowed("https://example.com/api/v1/user") is False
        assert await cache.is_allowed("https://example.com/private/data") is False


@pytest.mark.asyncio
async def test_robots_5xx_fail_closed():
    """RFC 9309 §2.3.1.4: erişilemeyen robots.txt -> tamamen yasak."""
    cache = RobotsCache()
    with respx.mock:
        respx.get("https://down.example.com/robots.txt").respond(503)
        assert await cache.is_allowed("https://down.example.com/herhangi") is False


@pytest.mark.asyncio
async def test_robots_429_fail_closed():
    cache = RobotsCache()
    with respx.mock:
        respx.get("https://limit.example.com/robots.txt").respond(429)
        assert await cache.is_allowed("https://limit.example.com/page") is False


@pytest.mark.asyncio
async def test_robots_ag_hatasi_fail_closed():
    import httpx

    cache = RobotsCache()
    with respx.mock:
        respx.get("https://broken.example.com/robots.txt").side_effect = httpx.ConnectError(
            "baglanti yok"
        )
        assert await cache.is_allowed("https://broken.example.com/page") is False


@pytest.mark.asyncio
async def test_robots_404_erisim_serbest():
    cache = RobotsCache()
    with respx.mock:
        respx.get("https://norobots.example.com/robots.txt").respond(404)
        assert await cache.is_allowed("https://norobots.example.com/page") is True


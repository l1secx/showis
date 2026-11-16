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

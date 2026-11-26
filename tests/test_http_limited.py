"""HttpClient.get_limited (P-009) ve paylaşılan client (R-005)."""

from __future__ import annotations

import httpx
import pytest
import respx
from showis_engine.http import HttpClient


@pytest.mark.asyncio
async def test_get_limited_govdeyi_okuma_sirasinda_sinirlar():
    client = HttpClient(enable_ssrf_guard=False)
    with respx.mock:
        respx.get("https://big.example.com/file").respond(
            200, content=b"x" * 100_000
        )
        resp = await client.get_limited(
            "https://big.example.com/file", max_bytes=1000, check_robots=False
        )
    assert resp.status_code == 200
    assert len(resp.content) == 1000
    assert resp.extensions.get("showis_truncated") is True
    # Sentetik gövdeye göre content-length yeniden hesaplanır (orijinal 100000 değil).
    assert resp.headers.get("content-length") == "1000"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_limited_kucuk_govde_kirpilmaz():
    client = HttpClient(enable_ssrf_guard=False)
    with respx.mock:
        respx.get("https://small.example.com/file").respond(200, content=b"abc")
        resp = await client.get_limited(
            "https://small.example.com/file", max_bytes=1000, check_robots=False
        )
    assert resp.content == b"abc"
    assert resp.extensions.get("showis_truncated") is False
    await client.aclose()


@pytest.mark.asyncio
async def test_get_limited_yonlendirme_izler():
    client = HttpClient(enable_ssrf_guard=False)
    with respx.mock:
        respx.get("https://a.example.com/start").respond(
            302, headers={"Location": "https://b.example.com/final"}
        )
        respx.get("https://b.example.com/final").respond(200, content=b"hedef")
        resp = await client.get_limited(
            "https://a.example.com/start",
            max_bytes=1000,
            check_robots=False,
            follow_redirects=True,
        )
    assert resp.content == b"hedef"
    assert str(resp.url) == "https://b.example.com/final"
    await client.aclose()


@pytest.mark.asyncio
async def test_paylasilan_client_ayni_loopta_yeniden_kullanilir():
    client = HttpClient(enable_ssrf_guard=False)
    first = client._client()
    second = client._client()
    assert first is second
    await client.aclose()
    assert client._http_client is None


@pytest.mark.asyncio
async def test_get_limited_robots_engeli():
    client = HttpClient(enable_ssrf_guard=False)
    with respx.mock:
        respx.get("https://robots.example.com/robots.txt").respond(
            200, text="User-agent: *\nDisallow: /private\n"
        )
        with pytest.raises(PermissionError):
            await client.get_limited(
                "https://robots.example.com/private/x",
                max_bytes=100,
                check_robots=True,
            )
    await client.aclose()


def test_httpx_response_tipi():
    """Sentetik yanıt gerçek httpx.Response olmalı (çağıranlar .json/.text kullanır)."""
    response = httpx.Response(200, content=b'{"ok": true}')
    assert response.json() == {"ok": True}

import pytest
import respx
from showis_engine.browser import BrowserClient, RenderResult
from showis_engine.browser_service import app as browser_app

# ---------------------------------------------------------------------------
# browser.py (BrowserClient) + browser_service.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_client_render_success():
    client = BrowserClient(base_url="http://browser.test", min_interval_per_host=0)
    with respx.mock:
        respx.post("http://browser.test/render").respond(
            json={
                "html": "<html>ok</html>",
                "screenshot_b64": None,
                "blocked": False,
                "status_code": 200,
                "url": "https://example.com/p/foo",
            }
        )
        result = await client.render("https://example.com/p/foo")
    assert isinstance(result, RenderResult)
    assert result.html == "<html>ok</html>"
    assert result.blocked is False
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_browser_client_render_blocked():
    client = BrowserClient(base_url="http://browser.test", min_interval_per_host=0)
    with respx.mock:
        respx.post("http://browser.test/render").respond(status_code=503)
        result = await client.render("https://example.com/p/foo")
    assert result.blocked is True


@pytest.mark.asyncio
async def test_browser_client_service_down():
    client = BrowserClient(base_url="http://browser.test", timeout=2.0)
    with respx.mock:
        respx.post("http://browser.test/render").mock(side_effect=Exception("conn refused"))
        result = await client.render("https://example.com/p/foo")
    assert result.blocked is True
    assert result.status_code == 0


@pytest.mark.asyncio
async def test_browser_client_ssrf_rejected():
    client = BrowserClient(base_url="http://browser.test")
    result = await client.render("http://127.0.0.1:8080/admin")
    assert result.blocked is True
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_browser_service_health():
    import httpx

    transport = httpx.ASGITransport(app=browser_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_browser_service_render_ssrf():
    import httpx

    transport = httpx.ASGITransport(app=browser_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/render", json={"url": "http://169.254.169.254/latest"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert body["status_code"] == 403


@pytest.mark.asyncio
async def test_browser_service_render_blocked_429():
    import httpx
    import showis_engine.browser_service as svc

    svc._last_render.clear()
    transport = httpx.ASGITransport(app=browser_app)
    with respx.mock:
        respx.get("https://thm.example.com/p/foo").respond(status_code=429)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/render", json={"url": "https://thm.example.com/p/foo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert body["status_code"] == 429


@pytest.mark.asyncio
async def test_browser_service_render_ok():
    import httpx
    import showis_engine.browser_service as svc

    svc._last_render.clear()
    transport = httpx.ASGITransport(app=browser_app)
    with respx.mock:
        respx.get("https://ok.example.com/p/foo").respond(
            text="<html><body>Profile OK</body></html>"
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/render", json={"url": "https://ok.example.com/p/foo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is False
    assert body["status_code"] == 200
    assert "Profile OK" in body["html"]

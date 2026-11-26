"""Browser render service — FastAPI app exposing POST /render.

Tries Playwright (async) if available; falls back to httpx when it is not.
Guards against SSRF using showis_engine.policy.guard_url().
Spec: Bölüm 8.4 — tek seferlik context, networkidle, host başına 1 req/s,
gerçek tarayıcı User-Agent, CAPTCHA/stealth/proxy YOK.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from showis_engine.browser import RENDER_USER_AGENT
from showis_engine.http import HttpClient, RobotsCache
from showis_engine.policy import PolicyViolationError, SSRFViolationError, guard_url

logger = logging.getLogger("showis.browser_service")

BROWSER_UA = RENDER_USER_AGENT
_ROBOTS = RobotsCache(BROWSER_UA)

BLOCK_PATTERNS = ["checkpoint", "vercel", "access denied"]
MIN_INTERVAL_PER_HOST = 1.0  # saniye
#: httpx fallback'te gövde üst sınırı (P-009; streaming okuma).
MAX_RENDER_BYTES = 5_000_000

app = FastAPI(title="Showis Browser Service")

# host -> son istek monotonic zamanı
_last_render: dict[str, float] = {}
_throttle_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class RenderRequest(BaseModel):
    url: str
    timeout_ms: int = 30000


class RenderResponse(BaseModel):
    html: str
    screenshot_b64: str | None
    blocked: bool
    status_code: int
    url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_blocked(status_code: int, html: str) -> bool:
    if status_code == 429:
        return True
    lower = html.lower()
    return any(pat in lower for pat in BLOCK_PATTERNS)


def _timeout_response(url: str) -> RenderResponse:
    return RenderResponse(
        html="",
        screenshot_b64=None,
        blocked=True,
        status_code=0,
        url=url,
    )


async def _throttle(url: str) -> None:
    host = urlparse(url).netloc.lower()
    async with _throttle_lock:
        now = time.monotonic()
        last = _last_render.get(host, 0.0)
        wait = MIN_INTERVAL_PER_HOST - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_render[host] = time.monotonic()


# ---------------------------------------------------------------------------
# Playwright path (optional)
# ---------------------------------------------------------------------------


async def _guard_route(route) -> None:  # noqa: ANN001 - Playwright Route tipi opsiyonel bagimlilik
    """Playwright isteğini yürütmeden **önce** SSRF + robots kapısından geçirir.

    DEC-003: alt kaynak istekleri (img/script/fetch/XHR) ve yönlendirmeler de
    kapsama girer. http(s) dışı şemalar (data:, blob:, about:) olduğu gibi geçer.
    """
    url = route.request.url
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        await route.continue_()
        return
    try:
        # DNS çözümü bloklayıcıdır; event loop'u meşgul etme.
        await asyncio.to_thread(guard_url, url)
        if not await _ROBOTS.is_allowed(url):
            raise PolicyViolationError(f"robots.txt engelledi: {url}")
    except PolicyViolationError as exc:
        logger.warning("Render istegi engellendi (policy): %s", exc)
        await route.abort("blockedbyclient")
        return
    await route.continue_()


async def _render_with_playwright(url: str, timeout_ms: int) -> RenderResponse:
    """Render via Playwright and return full response including screenshot."""
    from playwright.async_api import async_playwright  # type: ignore[import]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            # Tek seferlik context — kalıcı state yok
            context = await browser.new_context(
                user_agent=BROWSER_UA,
                viewport={"width": 1366, "height": 900},
            )
            # DEC-003: her istek (ana doküman, alt kaynak, yönlendirme) guard+robots.
            await context.route("**/*", _guard_route)
            page = await context.new_page()
            response = await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            # Savunma derinliği: route kapısına ek olarak son URL de kontrol edilir.
            if urlparse(page.url).hostname != urlparse(url).hostname:
                guard_url(page.url)
            status_code = response.status if response else 0
            html = await page.content()
            screenshot_bytes = await page.screenshot(full_page=False)
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
            await context.close()
        finally:
            await browser.close()

    return RenderResponse(
        html=html,
        screenshot_b64=screenshot_b64,
        blocked=_is_blocked(status_code, html),
        status_code=status_code,
        url=url,
    )


# ---------------------------------------------------------------------------
# httpx fallback
# ---------------------------------------------------------------------------


async def _render_with_httpx(url: str, timeout_ms: int) -> RenderResponse:
    """Render via plain httpx GET (no JS execution, no screenshot)."""
    # HttpClient: SSRF guard + robots + her yonlendirme adiminda yeniden guard.
    # Tek deneme: 429/403 yaniti "blocked" olarak raporlanmali, yeniden denenmemeli.
    # P-009: gövde streaming okunur ve MAX_RENDER_BYTES ile sınırlanır.
    client = HttpClient(user_agent=BROWSER_UA, timeout=timeout_ms / 1000.0)
    try:
        resp = await client.get_limited(
            url,
            max_bytes=MAX_RENDER_BYTES,
            follow_redirects=True,
        )
        html = resp.text
        return RenderResponse(
            html=html,
            screenshot_b64=None,
            blocked=_is_blocked(resp.status_code, html),
            status_code=resp.status_code,
            url=str(resp.url),
        )
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Capability detection
# ---------------------------------------------------------------------------


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401  # type: ignore[import]

        return True
    except ModuleNotFoundError:
        return False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "showis-browser"}


@app.post("/render", response_model=RenderResponse)
async def render(req: RenderRequest) -> RenderResponse:
    """Render a URL and return HTML + optional screenshot."""
    try:
        guard_url(req.url)
    except SSRFViolationError as exc:
        logger.warning("SSRF guard blocked URL %s: %s", req.url, exc)
        return RenderResponse(
            html="",
            screenshot_b64=None,
            blocked=True,
            status_code=403,
            url=req.url,
        )

    # robots.txt ile yasakli sayfalar render edilmez (AUP §1.3)
    if not await _ROBOTS.is_allowed(req.url):
        logger.warning("robots.txt engelledi, render atlandi: %s", req.url)
        return RenderResponse(
            html="",
            screenshot_b64=None,
            blocked=True,
            status_code=403,
            url=req.url,
        )

    await _throttle(req.url)

    try:
        if _playwright_available():
            return await _render_with_playwright(req.url, req.timeout_ms)
        return await _render_with_httpx(req.url, req.timeout_ms)
    except (TimeoutError, httpx.TimeoutException):
        return _timeout_response(req.url)
    except Exception as exc:
        logger.error("Render error for %s: %s", req.url, exc)
        return _timeout_response(req.url)

"""Browser render servisi istemcisi.

API/connector tarafı `BROWSER_SERVICE_URL` (varsayılan http://browser:8081)
üzerinden `POST /render` çağırır. Aynı host'a saniyede en fazla 1 istek
kuralı burada uygulanır (Bölüm 8.4).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from .policy import PolicyViolationError, guard_url

logger = logging.getLogger("showis.browser")

DEFAULT_BROWSER_SERVICE_URL = "http://browser:8081"

# Normal tarayıcı davranışı için gerçekçi UA (bot taklidi değil)
RENDER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class RenderResult(BaseModel):
    html: str = ""
    screenshot_b64: str | None = None
    blocked: bool = False
    status_code: int = 0
    url: str = ""


class BrowserClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 45.0,
        min_interval_per_host: float = 1.0,
    ):
        resolved = base_url or os.getenv("BROWSER_SERVICE_URL") or DEFAULT_BROWSER_SERVICE_URL
        self.base_url = resolved.rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval_per_host
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _host(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _throttle(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last_request.get(host, 0.0)
            wait = self.min_interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request[host] = time.monotonic()

    async def render(self, url: str, *, timeout_ms: int = 30000) -> RenderResult:
        """URL'yi tarayıcı servisiyle render eder. Hata/timeout → blocked sonuç."""
        try:
            guard_url(url)
        except PolicyViolationError as exc:
            logger.warning("Browser render engellendi (policy): %s", exc)
            return RenderResult(url=url, blocked=True, status_code=403)

        await self._throttle(self._host(url))

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/render",
                    json={"url": url, "timeout_ms": timeout_ms},
                )
            if resp.status_code != 200:
                return RenderResult(
                    url=url, blocked=True, status_code=resp.status_code
                )
            data = resp.json()
            return RenderResult(
                html=data.get("html", ""),
                screenshot_b64=data.get("screenshot_b64"),
                blocked=bool(data.get("blocked", False)),
                status_code=int(data.get("status_code", 0)),
                url=data.get("url", url),
            )
        except Exception as exc:
            logger.warning("Browser servisi çağrısı başarısız (%s): %s", url, exc)
            return RenderResult(url=url, blocked=True, status_code=0)

"""Host bazli istek sinirlayici (es zamanlilik + minimum istek araligi).

Paralel gorev yurutmede (``SHOWIS_SCAN_CONCURRENCY > 1``) ayni host'a ayni
anda yigilmak 429/403 hiz limitlerini tetikler ve nezaket (politeness)
sinirini asar. ``HostLimiter`` host (``urlparse(url).netloc``) basina:

* es zamanli istek sayisini ``max_per_host`` ile sinirlar,
* ayni host'a iki istek baslangici arasinda en az ``min_interval`` saniye birakir.

``HttpClient`` varsayilan olarak surec genelindeki limitere baglidir. Kapatmak
icin ``SHOWIS_HOST_LIMITER=0`` ortam degiskeni (veya ``HttpClient(...,
use_host_limiter=False)``). Esikler ``SHOWIS_HOST_MAX_CONCURRENCY`` ve
``SHOWIS_HOST_MIN_INTERVAL`` ile ayarlanir.

Not: limiter durumu olay dongusune (event loop) baglidir. Farkli bir loop'ta
kullanildiginda (orn. Celery gorevlerinde ``asyncio.run``) durum sifirlanir —
kapali bir loop'un semaphore'lari yeniden kullanilmaz.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

logger = logging.getLogger("showis.ratelimit")

#: Host basina es zamanli istek siniri (GitHub gibi host'larda guvenli sinir).
DEFAULT_MAX_PER_HOST = 2
#: Ayni host'a iki istek arasindaki asgari saniye (nezaket).
DEFAULT_MIN_INTERVAL = 0.25

#: Env ile verilebilecek guvenli araliklar (T-321).
MIN_MAX_PER_HOST, MAX_MAX_PER_HOST = 1, 8
MIN_MIN_INTERVAL, MAX_MIN_INTERVAL = 0.0, 5.0

_DISABLED_VALUES = {"0", "false", "off", "no", "disabled"}


def limiter_enabled() -> bool:
    """``SHOWIS_HOST_LIMITER`` ile limiter'i kapatma imkani (varsayilan: acik)."""
    return os.getenv("SHOWIS_HOST_LIMITER", "1").strip().lower() not in _DISABLED_VALUES


def host_key(url: str) -> str:
    """URL'den limiter anahtari uretir (netloc; yoksa yol)."""
    parsed = urlparse(url)
    return (parsed.netloc or parsed.path or "-").lower()


def _int_env(
    name: str, default: int, minimum: int | None = None, maximum: int | None = None
) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan %s kullanilir", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("%s=%s cok kucuk, %s kullanilacak", name, value, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("%s=%s cok buyuk, %s kullanilacak", name, value, maximum)
        return maximum
    return value


def _float_env(
    name: str, default: float, minimum: float | None = None, maximum: float | None = None
) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan %s kullanilir", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("%s=%s cok kucuk, %s kullanilacak", name, value, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("%s=%s cok buyuk, %s kullanilacak", name, value, maximum)
        return maximum
    return value


class HostLimiter:
    """Host bazli es zamanlilik ve istek araligi sinirlayici.

    Ayni host'a paralel istek sayisi ``max_per_host``'i gecmez ve iki istek
    baslangici arasinda en az ``min_interval`` saniye bulunur. Farkli host'lar
    birbirini etkilemez (her host kendi semaphore'una sahiptir), bu yuzden
    paralel taramanin hiz kazanimi korunur.
    """

    def __init__(self, max_per_host: int = DEFAULT_MAX_PER_HOST, min_interval: float = 0.25):
        if max_per_host < 1:
            raise ValueError("max_per_host >= 1 olmali")
        if min_interval < 0:
            raise ValueError("min_interval >= 0 olmali")
        self.max_per_host = max_per_host
        self.min_interval = min_interval
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._gates: dict[str, asyncio.Lock] = {}
        self._last_start: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> HostLimiter:
        """Env'den kurar: ``SHOWIS_HOST_MAX_CONCURRENCY`` (1..8) ve
        ``SHOWIS_HOST_MIN_INTERVAL`` (0..5).

        Varsayilanlar bugunku davranisi korur (2 / 0.25 sn): ayni host'a iki
        es zamanli istek + 250 ms baslangic araligi. Farkli host'lar birbirinden
        bagimsizdir, bu yuzden gorev paralelligi (O2=6) hiz kazanci saglar.
        """
        limiter = cls(
            max_per_host=_int_env(
                "SHOWIS_HOST_MAX_CONCURRENCY",
                DEFAULT_MAX_PER_HOST,
                MIN_MAX_PER_HOST,
                MAX_MAX_PER_HOST,
            ),
            min_interval=_float_env(
                "SHOWIS_HOST_MIN_INTERVAL",
                DEFAULT_MIN_INTERVAL,
                MIN_MIN_INTERVAL,
                MAX_MIN_INTERVAL,
            ),
        )
        logger.info(
            "Host limiter: max_per_host=%s, min_interval=%.2fs, enabled=%s",
            limiter.max_per_host,
            limiter.min_interval,
            limiter_enabled(),
        )
        return limiter

    # ------------------------------------------------------------------
    # Ic mekanizma
    # ------------------------------------------------------------------

    def _ensure_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Loop degistiginde eski semaphore/lock'lari birak.

        ``asyncio`` primitifleri yaratildiklari loop'a baglidir; module-global
        bir limiter farkli bir loop'ta (Celery ``asyncio.run``, testler) tekrar
        kullanilirsa ``RuntimeError`` verirdi.
        """
        if loop is self._loop:
            return
        self._loop = loop
        self._semaphores.clear()
        self._gates.clear()
        self._last_start.clear()

    def _semaphore(self, host: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(host)
        if sem is None:
            sem = asyncio.Semaphore(self.max_per_host)
            self._semaphores[host] = sem
        return sem

    def _gate(self, host: str) -> asyncio.Lock:
        gate = self._gates.get(host)
        if gate is None:
            gate = asyncio.Lock()
            self._gates[host] = gate
        return gate

    # ------------------------------------------------------------------
    # Genel API
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        """Istege host kotasi alir; ``async with`` govdesinde istek atilir."""
        loop = asyncio.get_running_loop()
        self._ensure_loop(loop)
        host = host_key(url)
        semaphore = self._semaphore(host)
        await semaphore.acquire()
        try:
            if self.min_interval > 0:
                async with self._gate(host):
                    now = loop.time()
                    last = self._last_start.get(host)
                    if last is not None:
                        wait = last + self.min_interval - now
                        if wait > 0:
                            await asyncio.sleep(wait)
                    self._last_start[host] = loop.time()
            yield
        finally:
            semaphore.release()


_default_limiter: HostLimiter | None = None


def default_host_limiter() -> HostLimiter:
    """Surec genelindeki limiter (ilk kullanimda ortam degiskenlerinden kurulur)."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = HostLimiter.from_env()
    return _default_limiter


def reset_default_host_limiter() -> None:
    """Varsayilan limitere sifirla (testler ve yeniden yapilandirma icin)."""
    global _default_limiter
    _default_limiter = None

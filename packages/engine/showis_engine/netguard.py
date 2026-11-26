"""Bağlantı katmanında SSRF savunması: DNS'i bir kez çöz, doğrulanmış IP'ye bağlan.

Neden: `policy.guard_url` host'u bir kez çözüp özel/loopback/link-local IP'leri
reddeder, ancak httpx isteği kendi DNS çözümüyle atar. Arada DNS değişirse
(rebinding) guard atlanır. Bu modül `httpcore.AsyncNetworkBackend`'i sarar:

* host zaten IP ise doğrudan doğrular,
* alan adı ise **tek** `getaddrinfo` yapar, her adresi politika süzgecinden geçirir,
* bağlantıyı seçilen IP'ye kurar (ikinci çözümleme yok); TLS SNI ve Host başlığı
  URL'nin asıl host'u olarak kalır çünkü URL'yi değiştirmiyoruz.

`httpx`/`httpcore` sürüm bağı: `pinned_async_transport` içindeki `_network_backend`
değişimi httpx 0.28 / httpcore 1.0 ile doğrulandı (requirements.lock pinli).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any

import httpcore
import httpx

from .policy import SSRFViolationError, is_blocked_ip

logger = logging.getLogger("showis.netguard")

#: DNS çözümü için üst süre (httpx connect timeout'undan ayrı, hızlı düş).
DNS_TIMEOUT_SECONDS = 5.0


def _ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(str(host).strip("[]"))
    except ValueError:
        return None


async def _resolve_public_addresses(host: str, port: int) -> list[str]:
    """Host'u tek kez çözer; özel/ayrılmış adres varsa SSRFViolationError."""
    literal = _ip_literal(host)
    if literal is not None:
        if is_blocked_ip(literal):
            raise SSRFViolationError(
                f"Özel/dahili IP adresine bağlantı engellendi: {literal}"
            )
        return [str(literal)]

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=DNS_TIMEOUT_SECONDS,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise httpcore.ConnectError(
            f"DNS çözümlenemedi ({host}): {type(exc).__name__}"
        ) from exc

    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            ip = ipaddress.ip_address(str(sockaddr[0]))
        except ValueError:
            continue
        if is_blocked_ip(ip):
            # Host hem public hem private adrese çözülüyorsa fail-closed.
            raise SSRFViolationError(
                f"Alan adı dahili IP'ye çözümlendi: {host} -> {ip}"
            )
        text = str(ip)
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise httpcore.ConnectError(f"DNS sonucu boş: {host}")
    return addresses


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Her TCP bağlantısını doğrulanmış tek bir IP'ye sabitler."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend):
        self._inner = inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await _resolve_public_addresses(host, port)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    host=address,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(
            path=path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def pinned_async_transport(**kwargs: Any) -> httpx.AsyncHTTPTransport:
    """SSRF pinli `httpx.AsyncHTTPTransport` üretir.

    Proxy/uds yapılandırılmışsa (bağlantı havuzu `_network_backend` taşımıyorsa)
    dokunmadan döner; bu durumda guard katmanı (`guard_url`) yürürlükte kalır.
    """
    transport = httpx.AsyncHTTPTransport(**kwargs)
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if pool is not None and backend is not None:
        pool._network_backend = PinnedNetworkBackend(backend)
    else:  # pragma: no cover - proxy/uds yolu
        logger.debug("Pinli SSRF backend uygulanamadi (proxy/uds etkin)")
    return transport

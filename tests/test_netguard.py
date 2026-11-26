"""netguard: bağlantı katmanında SSRF DNS pinleme (P-004)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpcore
import pytest
from showis_engine.netguard import PinnedNetworkBackend, _resolve_public_addresses
from showis_engine.policy import SSRFViolationError, guard_url, is_blocked_ip


class _FakeBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.connected: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        return object()

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return object()

    async def sleep(self, seconds: float) -> None:
        return None


def test_is_blocked_ip_siniflari():
    assert is_blocked_ip(ipaddress.ip_address("127.0.0.1")) is True
    assert is_blocked_ip(ipaddress.ip_address("10.1.2.3")) is True
    assert is_blocked_ip(ipaddress.ip_address("169.254.169.254")) is True
    assert is_blocked_ip(ipaddress.ip_address("::1")) is True
    assert is_blocked_ip(ipaddress.ip_address("93.184.216.34")) is False


def test_is_blocked_ip_cgnat_tailnet_ve_mapped():
    """T17-02: 100.64.0.0/10 CGNAT/Tailscale aralığı reddedilir."""
    assert is_blocked_ip(ipaddress.ip_address("100.64.0.1")) is True
    assert is_blocked_ip(ipaddress.ip_address("100.127.255.254")) is True
    assert is_blocked_ip(ipaddress.ip_address("::ffff:100.64.0.1")) is True
    assert is_blocked_ip(ipaddress.ip_address("93.184.216.34")) is False


def test_guard_url_cgnat_reddeder():
    with pytest.raises(SSRFViolationError):
        guard_url("http://100.64.0.1/")
    with pytest.raises(SSRFViolationError):
        guard_url("http://[::ffff:100.64.0.1]/")


@pytest.mark.asyncio
async def test_pinli_backend_cgnat_literal_reddeder():
    backend = PinnedNetworkBackend(_FakeBackend())
    with pytest.raises(SSRFViolationError):
        await backend.connect_tcp("100.64.0.1", 80)


@pytest.mark.asyncio
async def test_pinli_backend_dogrulanmis_ipye_baglanir(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    inner = _FakeBackend()
    backend = PinnedNetworkBackend(inner)
    await backend.connect_tcp("example.com", 443)
    assert inner.connected == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_pinli_backend_private_dns_reddeder(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port)),
        ]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    backend = PinnedNetworkBackend(_FakeBackend())
    with pytest.raises(SSRFViolationError):
        await backend.connect_tcp("internal.example", 80)


@pytest.mark.asyncio
async def test_pinli_backend_ip_literal_private_reddeder():
    backend = PinnedNetworkBackend(_FakeBackend())
    with pytest.raises(SSRFViolationError):
        await backend.connect_tcp("127.0.0.1", 80)


@pytest.mark.asyncio
async def test_pinli_backend_dns_hatasi_fail_closed(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kwargs):
        raise socket.gaierror("yok")

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    backend = PinnedNetworkBackend(_FakeBackend())
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("yok.example", 80)


@pytest.mark.asyncio
async def test_resolve_literal_public():
    assert await _resolve_public_addresses("93.184.216.34", 443) == ["93.184.216.34"]

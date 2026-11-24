"""Pwned Passwords k-anonimlik (range) ucu.

Tarayıcı parolanın SHA-1'ini yerelde hesaplar ve YALNIZ ilk 5 hex karakteri
gönderir. Sunucu da yalnız bu 5 karakteri upstream'e iletir; tam parola/hash
hiçbir yerde görülmez veya saklanmaz. `Add-Padding` ile yanıt boyutu gizlenir.
"""

from __future__ import annotations

import re

import httpx
from showis.core.redact import sanitize_error
from showis.services.breach.common import USER_AGENT, BreachProviderError

API_URL = "https://api.pwnedpasswords.com/range/"
PREFIX_RE = re.compile(r"^[0-9A-Fa-f]{5}$")
_SUFFIX_RE = re.compile(r"^[0-9A-F]{35}$")


def validate_prefix(prefix: str) -> str:
    if not PREFIX_RE.fullmatch(prefix or ""):
        raise ValueError("prefix tam 5 hex karakter olmalı")
    return prefix.upper()


def parse_range(body: str) -> list[dict[str, int | str]]:
    """`SUFFIX:COUNT` satırlarını ayrıştırır; padding (sayı 0) satırları atılır."""
    rows: list[dict[str, int | str]] = []
    for line in body.splitlines():
        suffix, _, count = line.strip().partition(":")
        suffix = suffix.upper()
        if not _SUFFIX_RE.fullmatch(suffix):
            continue
        try:
            number = int(count)
        except ValueError:
            continue
        if number > 0:
            rows.append({"suffix": suffix, "count": number})
    return rows


async def fetch_range(prefix: str) -> list[dict[str, int | str]]:
    prefix = validate_prefix(prefix)
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT, "Add-Padding": "true"},
        ) as client:
            response = await client.get(f"{API_URL}{prefix}")
    except Exception as exc:  # noqa: BLE001
        raise BreachProviderError(
            f"Pwned Passwords bağlantısı başarısız ({sanitize_error(exc)})"
        ) from exc
    if response.status_code != 200:
        raise BreachProviderError(f"Pwned Passwords HTTP {response.status_code} döndürdü")
    return parse_range(response.text)

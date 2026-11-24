"""Sızıntı sağlayıcıları için ortak yardımcılar."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx
from showis.core.redact import sanitize_error

USER_AGENT = "Showis/0.1 (+https://github.com/showis-osint)"
TIMEOUT_SECONDS = 20.0
MAX_BREACHES = 80
MAX_DESCRIPTION = 1600


class BreachProviderError(RuntimeError):
    """Tek bir sağlayıcının hatası; zincir diğerlerine devam eder."""


def plain_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return " ".join(text.split())[:MAX_DESCRIPTION]


def breach_row(
    *,
    name: str,
    title: str | None = None,
    domain: str | None = None,
    breach_date: str | None = None,
    added_date: str | None = None,
    pwn_count: int | None = None,
    data_classes: list[str] | None = None,
    description: str = "",
    is_verified: bool = False,
    is_sensitive: bool = False,
    is_spam_list: bool = False,
    is_malware: bool = False,
    source: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "title": title or name,
        "domain": domain or None,
        "breach_date": breach_date,
        "added_date": added_date,
        "pwn_count": pwn_count,
        "data_classes": list(data_classes or []),
        "description": plain_text(description),
        "is_verified": is_verified,
        "is_sensitive": is_sensitive,
        "is_spam_list": is_spam_list,
        "is_malware": is_malware,
        "sources": [source],
    }


async def get_json(
    url: str,
    *,
    provider: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    not_found_ok: bool = True,
) -> Any:
    """GET + JSON; 404 → None. Diğer hatalar BreachProviderError."""
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=merged) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # noqa: BLE001 - bağlantı/timeout
        raise BreachProviderError(
            f"{provider} bağlantısı başarısız ({type(exc).__name__}: {sanitize_error(exc)})"
        ) from exc
    if response.status_code == 404 and not_found_ok:
        return None
    if response.status_code == 429:
        raise BreachProviderError(f"{provider} hız sınırı; bir süre sonra tekrar deneyin")
    if response.status_code == 401:
        raise BreachProviderError(f"{provider} kimlik doğrulaması reddedildi")
    if response.status_code != 200:
        raise BreachProviderError(f"{provider} HTTP {response.status_code} döndürdü")
    try:
        return response.json()
    except ValueError as exc:
        raise BreachProviderError(f"{provider} geçersiz JSON döndürdü") from exc

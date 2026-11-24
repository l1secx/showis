"""Hudson Rock Cavalier ücretsiz OSINT ucu: infostealer bulguları.

Yalnız özet alanlar tutulur (tarih, stealer ailesi, işletim sistemi, etkilenen
servis sayıları). Parola, giriş adı, IP, bilgisayar adı ve dosya yolu bilinçli
olarak atılır.
"""

from __future__ import annotations

from typing import Any

from showis.services.breach.common import get_json

SOURCE = "hudsonrock"
API_URL = "https://cavalier.hudsonrock.com/api/json/v2/osint-tools/search-by-email"
MAX_STEALERS = 20


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def summarize(payload: Any) -> dict[str, Any]:
    stealers_raw = payload.get("stealers") if isinstance(payload, dict) else None
    stealers: list[dict[str, Any]] = []
    if isinstance(stealers_raw, list):
        for raw in stealers_raw[:MAX_STEALERS]:
            if not isinstance(raw, dict):
                continue
            stealers.append(
                {
                    "date_compromised": raw.get("date_compromised"),
                    "stealer_family": raw.get("stealer_family"),
                    "operating_system": raw.get("operating_system"),
                    "corporate_services": _int(raw.get("total_corporate_services")),
                    "user_services": _int(raw.get("total_user_services")),
                }
            )
    return {
        "count": len(stealers),
        "stealers": stealers,
        "source": SOURCE,
        "source_url": "https://www.hudsonrock.com/free-tools",
    }


async def fetch_infostealer(email: str) -> dict[str, Any]:
    payload = await get_json(API_URL, provider=SOURCE, params={"email": email})
    if payload is None:
        return summarize({})
    return summarize(payload)

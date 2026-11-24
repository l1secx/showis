"""XposedOrNot ücretsiz, anahtarsız ihlal API'si.

Önce `breach-analytics` (ayrıntılı: veri türleri, tarih, kayıt sayısı) denenir;
başarısız olursa yalın `check-email` ucuna düşülür.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from showis.services.breach.common import (
    MAX_BREACHES,
    BreachProviderError,
    breach_row,
    get_json,
)

SOURCE = "xposedornot"
API_BASE = "https://api.xposedornot.com/v1"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_analytics(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    exposed = payload.get("ExposedBreaches")
    if not isinstance(exposed, dict):
        return []
    details = exposed.get("breaches_details")
    if not isinstance(details, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in details[:MAX_BREACHES]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("breach") or "").strip()
        if not name:
            continue
        data_classes = [
            part.strip()
            for part in str(raw.get("xposed_data") or "").split(";")
            if part.strip()
        ]
        year = str(raw.get("xposed_date") or "").strip()
        rows.append(
            breach_row(
                name=name,
                domain=str(raw.get("domain") or "") or None,
                breach_date=year or None,
                pwn_count=_int_or_none(raw.get("xposed_records")),
                data_classes=data_classes,
                description=str(raw.get("details") or ""),
                is_verified=str(raw.get("verified") or "").lower() == "yes",
                source=SOURCE,
            )
        )
    return rows


def _parse_simple(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    groups = payload.get("breaches")
    names: list[str] = []
    if isinstance(groups, list):
        for group in groups:
            items = group if isinstance(group, list) else [group]
            for name in items:
                text = str(name or "").strip()
                if text and text not in names:
                    names.append(text)
    return [breach_row(name=name, source=SOURCE) for name in names[:MAX_BREACHES]]


async def fetch_breaches(email: str) -> list[dict[str, Any]]:
    try:
        payload = await get_json(
            f"{API_BASE}/breach-analytics", provider=SOURCE, params={"email": email}
        )
        if payload is None:
            return []
        rows = _parse_analytics(payload)
        if rows:
            return rows
    except BreachProviderError:
        pass
    payload = await get_json(
        f"{API_BASE}/check-email/{quote(email, safe='')}", provider=SOURCE
    )
    if payload is None:
        return []
    return _parse_simple(payload)

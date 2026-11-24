"""Have I Been Pwned (HIBP) v3 — yalnız kullanıcı anahtar girdiyse ek kaynak.

Anahtar BYOK ayarlarından (`settings.provider="hibp"`) veya `HIBP_API_KEY`
ortam değişkeninden okunur.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

from showis.db.models import Setting
from showis.services.breach.common import (
    MAX_BREACHES,
    BreachProviderError,
    breach_row,
    get_json,
)
from showis_settings.crypto import decrypt_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.hibp")

SOURCE = "haveibeenpwned"
API_BASE = "https://haveibeenpwned.com/api/v3"


async def api_key_from_settings(db: AsyncSession) -> str:
    setting = (
        await db.execute(
            select(Setting).where(
                Setting.provider == "hibp",
                Setting.enabled.is_(True),
                Setting.secret_encrypted.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if setting is not None:
        try:
            key = str(decrypt_secret(setting.secret_encrypted) or "").strip()
        except Exception:  # noqa: BLE001 - bozuk sır anahtar yok sayılır
            logger.warning("HIBP anahtarı çözülemedi")
            key = ""
        if key:
            return key
    return (os.getenv("HIBP_API_KEY") or "").strip()


def _row(raw: dict[str, Any]) -> dict[str, Any]:
    data_classes = raw.get("DataClasses")
    return breach_row(
        name=str(raw.get("Name") or ""),
        title=str(raw.get("Title") or raw.get("Name") or ""),
        domain=raw.get("Domain"),
        breach_date=raw.get("BreachDate"),
        added_date=raw.get("AddedDate"),
        pwn_count=raw.get("PwnCount"),
        data_classes=[str(item) for item in data_classes]
        if isinstance(data_classes, list)
        else [],
        description=str(raw.get("Description") or ""),
        is_verified=bool(raw.get("IsVerified")),
        is_sensitive=bool(raw.get("IsSensitive")),
        is_spam_list=bool(raw.get("IsSpamList")),
        is_malware=bool(raw.get("IsMalware")),
        source=SOURCE,
    )


async def fetch_breaches(email: str, api_key: str) -> list[dict[str, Any]]:
    payload = await get_json(
        f"{API_BASE}/breachedaccount/{quote(email, safe='')}",
        provider="HIBP",
        params={"truncateResponse": "false"},
        headers={"hibp-api-key": api_key},
    )
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise BreachProviderError("HIBP beklenen liste biçimini döndürmedi")
    return [_row(item) for item in payload[:MAX_BREACHES] if isinstance(item, dict)]

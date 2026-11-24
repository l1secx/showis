"""Have I Been Pwned (HIBP) e-posta ihlal kontrolü.

HIBP v3 `breachedaccount` ucu API anahtarı ister. Anahtar BYOK ayarlarından
(`settings.provider="hibp"`) veya `HIBP_API_KEY` ortam değişkeninden okunur.
E-posta yalnız kullanıcı kontrolü başlattığında üçüncü tarafa gönderilir;
sonuç havuz kaydının `attributes.breach_check` alanına yazılır.
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from showis.core.redact import sanitize_error
from showis.db.models import Setting
from showis_settings.crypto import decrypt_secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.hibp")

API_BASE = "https://haveibeenpwned.com/api/v3"
USER_AGENT = "Showis/0.1 (+https://github.com/showis-osint)"
TIMEOUT_SECONDS = 20.0
MAX_BREACHES = 50
MAX_DESCRIPTION = 1600


class HIBPError(RuntimeError):
    pass


class HIBPKeyMissing(HIBPError):
    pass


def _plain_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))
    return " ".join(text.split())[:MAX_DESCRIPTION]


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


def _breach_summary(raw: dict[str, Any]) -> dict[str, Any]:
    data_classes = raw.get("DataClasses")
    return {
        "name": str(raw.get("Name") or ""),
        "title": str(raw.get("Title") or raw.get("Name") or ""),
        "domain": raw.get("Domain"),
        "breach_date": raw.get("BreachDate"),
        "added_date": raw.get("AddedDate"),
        "pwn_count": raw.get("PwnCount"),
        "data_classes": [str(item) for item in data_classes]
        if isinstance(data_classes, list)
        else [],
        "description": _plain_text(str(raw.get("Description") or "")),
        "is_verified": bool(raw.get("IsVerified")),
        "is_sensitive": bool(raw.get("IsSensitive")),
        "is_spam_list": bool(raw.get("IsSpamList")),
        "is_malware": bool(raw.get("IsMalware")),
    }


async def fetch_breaches(email: str, api_key: str) -> dict[str, Any]:
    """HIBP'den tek e-posta için ihlalleri getirir; anahtar yoksa hata verir."""
    candidate = str(email or "").strip()
    if not candidate or "@" not in candidate:
        raise HIBPError("geçerli bir e-posta gerekli")
    headers = {
        "hibp-api-key": api_key,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    url = f"{API_BASE}/breachedaccount/{quote(candidate, safe='')}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, headers=headers) as client:
            response = await client.get(url, params={"truncateResponse": "false"})
    except Exception as exc:  # noqa: BLE001 - bağlantı/timeout
        logger.warning("HIBP isteği başarısız: %s", sanitize_error(exc))
        raise HIBPError(f"HIBP bağlantısı başarısız ({type(exc).__name__})") from exc

    if response.status_code == 404:
        rows: list[dict[str, Any]] = []
    elif response.status_code == 200:
        try:
            payload = response.json()
        except ValueError as exc:
            raise HIBPError("HIBP geçersiz JSON döndürdü") from exc
        if not isinstance(payload, list):
            raise HIBPError("HIBP beklenen liste biçimini döndürmedi")
        rows = [item for item in payload if isinstance(item, dict)]
    elif response.status_code == 401:
        raise HIBPError("HIBP API anahtarı geçersiz")
    elif response.status_code == 429:
        raise HIBPError("HIBP hız sınırı; bir süre sonra tekrar deneyin")
    else:
        raise HIBPError(f"HIBP HTTP {response.status_code} döndürdü")

    breaches = [_breach_summary(item) for item in rows[:MAX_BREACHES]]
    breaches.sort(key=lambda item: str(item.get("breach_date") or ""), reverse=True)
    data_classes: list[str] = []
    for breach in breaches:
        for data_class in breach["data_classes"]:
            if data_class not in data_classes:
                data_classes.append(data_class)
    return {
        "email": candidate.lower(),
        "breached": bool(breaches),
        "count": len(breaches),
        "data_classes": data_classes,
        "breaches": breaches,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "haveibeenpwned",
        "source_url": "https://haveibeenpwned.com/",
    }


async def check_email(db: AsyncSession, email: str) -> dict[str, Any]:
    key = await api_key_from_settings(db)
    if not key:
        raise HIBPKeyMissing(
            "HIBP API anahtarı yok. Ayarlar > HIBP bölümünden ekleyin."
        )
    return await fetch_breaches(email, key)

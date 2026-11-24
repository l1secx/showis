"""Anahtarsız (ücretsiz) e-posta sızıntı kontrolü: sağlayıcı zinciri.

Varsayılan zincir anahtar gerektirmez:

1. XposedOrNot — e-posta → bilinen veri ihlalleri (ücretsiz, anahtarsız).
2. Hudson Rock Cavalier — e-posta → infostealer bulguları (ücretsiz OSINT ucu).
3. HIBP v3 — yalnız kullanıcı anahtar girdiyse ek kaynak olarak.

Bilinçli sınır: üçüncü kişilere ait düz metin parola, giriş adı, IP veya
bilgisayar adı hiçbir sağlayıcıdan alınıp saklanmaz/gösterilmez. Yalnız
"sızıntı var mı, hangi olayda, hangi veri türleri" bilgisi tutulur.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from showis.services.breach import hibp as hibp_provider
from showis.services.breach import hudsonrock, xposedornot
from showis.services.breach.common import BreachProviderError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("showis.breach")

__all__ = ["BreachError", "BreachProviderError", "check_email"]


class BreachError(RuntimeError):
    """Hiçbir sağlayıcı sonuç döndüremedi."""


def _merge_breaches(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for breach in group:
            key = str(breach.get("name") or breach.get("title") or "").strip().lower()
            if not key:
                continue
            current = merged.get(key)
            if current is None:
                merged[key] = dict(breach)
                continue
            for field, value in breach.items():
                if field == "data_classes":
                    for data_class in value or []:
                        if data_class not in current["data_classes"]:
                            current["data_classes"].append(data_class)
                elif field == "sources":
                    for source in value or []:
                        if source not in current["sources"]:
                            current["sources"].append(source)
                elif not current.get(field) and value:
                    current[field] = value
    rows = list(merged.values())
    rows.sort(key=lambda item: str(item.get("breach_date") or ""), reverse=True)
    return rows


async def check_email(db: AsyncSession, email: str) -> dict[str, Any]:
    """E-postayı anahtarsız zincirde kontrol eder; birleşik rapor döner."""
    candidate = str(email or "").strip().lower()
    if not candidate or "@" not in candidate:
        raise BreachError("geçerli bir e-posta gerekli")

    providers_ok: list[str] = []
    providers_failed: dict[str, str] = {}
    breach_groups: list[list[dict[str, Any]]] = []
    infostealer: dict[str, Any] | None = None

    try:
        breach_groups.append(await xposedornot.fetch_breaches(candidate))
        providers_ok.append(xposedornot.SOURCE)
    except BreachProviderError as exc:
        providers_failed[xposedornot.SOURCE] = str(exc)

    try:
        infostealer = await hudsonrock.fetch_infostealer(candidate)
        providers_ok.append(hudsonrock.SOURCE)
    except BreachProviderError as exc:
        providers_failed[hudsonrock.SOURCE] = str(exc)

    hibp_key = await hibp_provider.api_key_from_settings(db)
    if hibp_key:
        try:
            breach_groups.append(await hibp_provider.fetch_breaches(candidate, hibp_key))
            providers_ok.append(hibp_provider.SOURCE)
        except BreachProviderError as exc:
            providers_failed[hibp_provider.SOURCE] = str(exc)

    if not providers_ok:
        detail = "; ".join(f"{name}: {err}" for name, err in providers_failed.items())
        raise BreachError(f"Sızıntı sağlayıcılarına ulaşılamadı ({detail})")

    breaches = _merge_breaches(breach_groups)
    data_classes: list[str] = []
    for breach in breaches:
        for data_class in breach.get("data_classes") or []:
            if data_class not in data_classes:
                data_classes.append(data_class)
    password_exposed = any(
        "password" in str(data_class).lower() for data_class in data_classes
    )
    stealer_hits = int((infostealer or {}).get("count") or 0)
    return {
        "email": candidate,
        "breached": bool(breaches) or stealer_hits > 0,
        "count": len(breaches),
        "data_classes": data_classes,
        "password_exposed": password_exposed,
        "breaches": breaches,
        "infostealer": infostealer,
        "providers": providers_ok,
        "provider_errors": providers_failed,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "+".join(providers_ok),
        "source_url": "https://xposedornot.com/",
    }

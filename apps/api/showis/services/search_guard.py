"""Arama sağlayıcı hatalarını sırsızlaştıran sarmalayıcı (SEC-08).

Sorun: SerpAPI/CSE anahtarları URL query'sinde taşınıyor
(`?api_key=…`, `&key=…`). `httpx.HTTPStatusError` metni **tam URL'yi**
içerdiği için anahtar, görev hatasına (`tasks.error`) ve oradan API yanıtına
sızıyordu.

Çözüm: sağlayıcı çağrısı bu sarmalayıcıdan geçer; her istisna
`SearchProviderError` olarak, yalnız sağlayıcı adı + HTTP durum kodu ile
yeniden üretilir. Ham metin, URL veya gövde taşınmaz.
"""

from __future__ import annotations

import logging
from typing import Any

from showis.core.redact import provider_error_message, sanitize_error
from showis_connectors.websearch import SearchHit, SearchProvider

logger = logging.getLogger("showis.search_guard")


class SearchProviderError(RuntimeError):
    """Sırsız, istemciye yazılabilir arama sağlayıcısı hatası."""


class SearchProviderSanitizer:
    """``SearchProvider`` protokolünü koruyan hata sırsızlaştırıcı."""

    def __init__(self, inner: SearchProvider, label: str | None = None):
        self._inner = inner
        self.name: str = str(label or getattr(inner, "name", "search"))

    async def search(
        self,
        query: str,
        *,
        limit: int = 20,
        site: str | None = None,
    ) -> list[SearchHit]:
        try:
            hits = await self._inner.search(query, limit=limit, site=site)
            return list(hits)
        except SearchProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - her saglayici hatasi sirsizlastirilir
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            # Log da sırsız: ham metin yalnız redakte edilmiş hâliyle.
            logger.warning(
                "Arama saglayicisi hatasi (%s): %s", self.name, sanitize_error(exc)
            )
            raise SearchProviderError(
                provider_error_message(self.name, status_code)
            ) from None

    def __repr__(self) -> str:  # pragma: no cover - debug kolayligi
        return f"SearchProviderSanitizer({self.name})"

    #: Sarmalanan sağlayıcı (testler için)
    @property
    def inner(self) -> Any:
        return self._inner

"""Site-check sonuc onbellegi (TTL + LRU, surec ici).

Amac: ayni ``(kullanici adi, site)`` cifti kisa sure icinde tekrar
sorgulanmasin. Bu hem tarama suresini hem de hedef sitelere giden gereksiz
istek sayisini dusurur (AUP: nazik tarama).

Tasarim notlari:

- Anahtar ``(username.lower(), site_key)``; TTL varsayilan 6 saat
  (``SHOWIS_SITECHECK_CACHE_TTL`` saniye, ``0`` = onbellek kapali).
- Boyut siniri ``SHOWIS_SITECHECK_CACHE_MAX`` (varsayilan 5000 kayit, LRU).
- Yalnizca **kararli** sonuclar saklanir: ``exists``, ``absent``, ``skipped``.
  ``error`` (ag hatasi) ve ``unknown`` (orn. 403/429) saklanmaz — gecici
  durumlar onbellege yazilirsa tarama yanlislikla "yok" der.
- Onbellekten gelen yanit **yeni ag istegi sayilmaz**: ``HttpClient`` hic
  cagrilmaz, dolayisiyla ``requests_made()`` sayaci artmaz.
- Site guvenligi politikasi degismez: robots.txt ve SSRF guard karari
  istegi atan katmanda (``UsernameCheckEngine``) verilir; onbellek yalnizca
  daha once uretilmis ``CheckResult``'u tekrar kullanir.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger("showis.sitecheck.cache")

TTL_ENV = "SHOWIS_SITECHECK_CACHE_TTL"
MAX_ENV = "SHOWIS_SITECHECK_CACHE_MAX"

DEFAULT_TTL_SECONDS = 6 * 3600
DEFAULT_MAX_ENTRIES = 5000

#: Onbellege alinabilecek durumlar (kararli olanlar).
CACHEABLE_STATUSES = frozenset({"exists", "absent", "skipped"})


def ttl_from_env() -> float:
    """``SHOWIS_SITECHECK_CACHE_TTL`` (saniye). Gecersiz deger -> varsayilan."""
    raw = os.getenv(TTL_ENV)
    if raw is None or not str(raw).strip():
        return float(DEFAULT_TTL_SECONDS)
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning("%s sayisal degil (%r), %s kullanilacak", TTL_ENV, raw,
                       DEFAULT_TTL_SECONDS)
        return float(DEFAULT_TTL_SECONDS)
    if value < 0:
        logger.warning("%s negatif, onbellek kapali sayilacak", TTL_ENV)
        return 0.0
    return value


def max_entries_from_env() -> int:
    """``SHOWIS_SITECHECK_CACHE_MAX`` (kayit). Gecersiz deger -> varsayilan."""
    raw = os.getenv(MAX_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_ENTRIES
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning("%s sayisal degil (%r), %s kullanilacak", MAX_ENV, raw,
                       DEFAULT_MAX_ENTRIES)
        return DEFAULT_MAX_ENTRIES
    return max(value, 0)


@dataclass(frozen=True)
class CachedCheck:
    """Onbellekte tutulan ``CheckResult`` ozeti (SiteSpec ve govde haric).

    Govde ornegi **saklanmaz**: onbellekten gelen yanit icin yeni kanit
    uretilmez (ilk taramanin kaniti entity'de bagli kalir), bellek de
    kucuk kalir.
    """

    username: str
    site_key: str
    status: str
    url: str
    http_status: int | None
    detail: str
    final_url: str | None
    stored_at: float
    expires_at: float

    def is_fresh(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) < self.expires_at


class CheckCache:
    """TTL'li, boyut sinirli (LRU) ``(username, site_key)`` onbellegi."""

    def __init__(
        self,
        ttl_seconds: float | None = None,
        max_entries: int | None = None,
    ):
        self.ttl_seconds = float(ttl_from_env() if ttl_seconds is None else ttl_seconds)
        self.max_entries = int(max_entries_from_env() if max_entries is None else max_entries)
        self._entries: OrderedDict[tuple[str, str], CachedCheck] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0

    # ------------------------------------------------------------------
    # Yardimci
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0 and self.max_entries > 0

    @staticmethod
    def key(username: str, site_key: str) -> tuple[str, str]:
        return (username.strip().lstrip("@").lower(), site_key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def get(self, username: str, site_key: str) -> CachedCheck | None:
        if not self.enabled:
            return None
        key = self.key(username, site_key)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            if not entry.is_fresh(now):
                del self._entries[key]
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry

    def set(self, username: str, site_key: str, result: object) -> None:
        """``CheckResult`` benzeri nesneyi saklar (yalnizca kararli durumlar)."""
        if not self.enabled:
            return
        status = str(getattr(result, "status", ""))
        if status not in CACHEABLE_STATUSES:
            return
        key = self.key(username, site_key)
        now = time.monotonic()
        entry = CachedCheck(
            username=username,
            site_key=site_key,
            status=status,
            url=str(getattr(result, "url", "")),
            http_status=getattr(result, "http_status", None),
            detail=str(getattr(result, "detail", "") or ""),
            final_url=getattr(result, "final_url", None),
            stored_at=now,
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self.stores += 1
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
                "evictions": self.evictions,
                "ttl_seconds": self.ttl_seconds,
                "max_entries": self.max_entries,
            }


_default_cache: CheckCache | None = None
_default_lock = threading.Lock()


def get_default_cache() -> CheckCache:
    """Surec genelinde paylasilan onbellek (env degisirse yeniden kurulmaz)."""
    global _default_cache
    with _default_lock:
        if _default_cache is None:
            _default_cache = CheckCache()
        return _default_cache


def reset_default_cache() -> None:
    """Testler icin: varsayilan onbellegi (env ile birlikte) sifirla."""
    global _default_cache
    with _default_lock:
        _default_cache = None

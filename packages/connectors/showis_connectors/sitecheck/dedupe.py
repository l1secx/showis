"""Tarama (scan) bazli plan tekilleştirme.

Iki site-check connector'i (``sitecheck`` ve ``sitecheck_search``) ayni
kullanici adlarini planlayabiliyor; her ikisi de DB'de ayri gorev olusturur ve
ayni site setini ikinci kez tarar. Bu modul, *ayni tarama icinde* ayni
kullanici adini yalnizca bir connector'un planlamasina izin verir.

Kurallar:

- Kayit tarama bazlidir: ``scan_id`` -> {username: owner_connector}.
- Bir kullanici adi ilk talep eden connector'a aittir; digerleri atlar.
- Ayni connector yeniden plan yaparsa (retry) kendi adlarini korur.
- Bellek siniri: en fazla ``max_scans`` tarama tutulur (LRU).
- Kayit yalnizca planlamayi etkiler; yurutme/guvenlik kararlarina dokunmaz.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

DEFAULT_MAX_SCANS = 64


class ScanPlanRegistry:
    """``scan_id`` basina kullanici adi sahipligi tutan kucuk LRU kayit."""

    def __init__(self, max_scans: int = DEFAULT_MAX_SCANS):
        self.max_scans = max(max_scans, 1)
        self._scans: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._lock = threading.Lock()
        self.skipped = 0

    @staticmethod
    def normalize(username: str) -> str:
        return username.strip().lstrip("@").lower()

    def claim(self, scan_id: str, username: str, connector: str) -> bool:
        """``username`` bu connector tarafindan planlanabilir mi?"""
        key = self.normalize(username)
        if not key:
            return False
        with self._lock:
            owners = self._scans.get(scan_id)
            if owners is None:
                owners = {}
                self._scans[scan_id] = owners
                self._evict()
            else:
                self._scans.move_to_end(scan_id)
            owner = owners.get(key)
            if owner is None:
                owners[key] = connector
                return True
            if owner == connector:
                return True
            self.skipped += 1
            return False

    def planned(self, scan_id: str) -> set[str]:
        with self._lock:
            return set(self._scans.get(scan_id, {}))

    def forget(self, scan_id: str) -> None:
        with self._lock:
            self._scans.pop(scan_id, None)

    def clear(self) -> None:
        with self._lock:
            self._scans.clear()
            self.skipped = 0

    def _evict(self) -> None:
        while len(self._scans) > self.max_scans:
            self._scans.popitem(last=False)


_default_registry: ScanPlanRegistry | None = None
_default_lock = threading.Lock()


def get_default_registry() -> ScanPlanRegistry:
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = ScanPlanRegistry()
        return _default_registry


def reset_default_registry() -> None:
    global _default_registry
    with _default_lock:
        _default_registry = None

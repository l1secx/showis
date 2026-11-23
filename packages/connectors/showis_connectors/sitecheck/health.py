"""Site sağlığı — 403/429/5xx dönen siteleri geçici olarak devre dışı bırakır.

Her taramada aynı ölü/engelli siteye istek atmak bütçeyi tüketir. Bu modül
süreç içi, TTL'li bir sayaç tutar; eşik aşılırsa site ``skipped`` döner.
Kalıcı değildir: yeni süreçte sağlık sıfırlanır.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

DEFAULT_TTL_SECONDS = 3600
DEFAULT_FAILURE_THRESHOLD = 2
FAILURE_HTTP_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


class SiteHealth:
    def __init__(
        self,
        ttl_seconds: float | None = None,
        failure_threshold: int | None = None,
    ) -> None:
        if ttl_seconds is None:
            ttl_seconds = _env_float("SHOWIS_SITECHECK_HEALTH_TTL", DEFAULT_TTL_SECONDS)
        if failure_threshold is None:
            failure_threshold = _env_int(
                "SHOWIS_SITECHECK_HEALTH_THRESHOLD", DEFAULT_FAILURE_THRESHOLD
            )
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.failure_threshold = max(1, int(failure_threshold))
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0

    def _prune(self, key: str, now: float) -> deque[float]:
        failures = self._failures[key]
        cutoff = now - self.ttl_seconds
        while failures and failures[0] < cutoff:
            failures.popleft()
        return failures

    def blocked(self, key: str) -> bool:
        if not self.enabled:
            return False
        return len(self._prune(key, time.monotonic())) >= self.failure_threshold

    def record(self, key: str, status: str, http_status: int | None = None) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        failures = self._prune(key, now)
        failed = status in {"error"} or http_status in FAILURE_HTTP_STATUSES
        if failed:
            failures.append(now)
        else:
            failures.clear()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default

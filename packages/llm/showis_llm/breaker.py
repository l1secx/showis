"""Süreç içi devre kesici (circuit breaker) — LLM erişilemezken boşa bekleme yok.

Sorun (T-333, canlı log): her tarama başında planner LLM'i 3 kez × 30 sn timeout
ile deneniyor (~90 sn) ve sonunda şablon planına düşülüyordu. Tarama süresinin
~%25'i bu boşa beklemeydi.

Davranış:

* ``failure_threshold`` (varsayılan 3) **ardışık** hatadan sonra devre açılır,
* ``cooldown`` (env ``SHOWIS_LLM_COOLDOWN``, varsayılan 120 sn) boyunca hiçbir
  LLM çağrısı yapılmaz (``allow()`` False → çağıran anında fallback'e düşer),
* cooldown bitince **tek** yarı-açık denemeye izin verilir; başarılı olursa
  devre kapanır ve sayaç sıfırlanır, hata olursa cooldown yeniden başlar.

Sayaçlar süreç genelindedir (``stats()`` → ``scan.stats`` alanları):
``llm_calls``, ``llm_failures``, ``llm_skipped_by_breaker``.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("showis.llm.breaker")

THRESHOLD_ENV = "SHOWIS_LLM_BREAKER_THRESHOLD"
COOLDOWN_ENV = "SHOWIS_LLM_COOLDOWN"

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 120.0


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan %s kullanilir", name, raw, default)
        return default
    return max(minimum, value)


class CircuitBreaker:
    """Ardışık hata sayan, cooldown'lu devre kesici (thread-safe)."""

    def __init__(
        self,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ):
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        #: yarı-açık deneme verildi mi (cooldown sonrası tek deneme)
        self._half_open_used = False
        self.calls = 0
        self.failures = 0
        self.skipped = 0

    # ------------------------------------------------------------------
    # Durum
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._half_open_used:
                return "open"
            if self._cooldown_elapsed():
                return "half_open"
            return "open"

    def _cooldown_elapsed(self) -> bool:
        return (
            self._opened_at is not None
            and (time.monotonic() - self._opened_at) >= self.cooldown_seconds
        )

    def allow(self) -> bool:
        """Çağrıya izin var mı? (False → LLM hiç denenmez)"""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._cooldown_elapsed() and not self._half_open_used:
                # Yarı-açık: tek deneme hakkı tüketilir
                self._half_open_used = True
                logger.info("LLM devre yarı-açık: tek deneme yapılacak")
                return True
            self.skipped += 1
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_used = False

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self._failures += 1
            if self._opened_at is not None:
                # Yarı-açık deneme başarısız: cooldown yeniden başlar
                self._opened_at = time.monotonic()
                self._half_open_used = False
                logger.warning(
                    "LLM devre dışı (cooldown %ds)", int(self.cooldown_seconds)
                )
                return
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()
                self._half_open_used = False
                logger.warning(
                    "LLM devre dışı (cooldown %ds): %d ardışık hata",
                    int(self.cooldown_seconds),
                    self._failures,
                )

    def record_call(self) -> None:
        with self._lock:
            self.calls += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "llm_calls": self.calls,
                "llm_failures": self.failures,
                "llm_skipped_by_breaker": self.skipped,
            }

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_used = False
            self.calls = 0
            self.failures = 0
            self.skipped = 0


_default_breaker: CircuitBreaker | None = None
_default_lock = threading.Lock()


def get_breaker() -> CircuitBreaker:
    """Süreç geneli devre kesici (env'den bir kez kurulur)."""
    global _default_breaker
    if _default_breaker is None:
        with _default_lock:
            if _default_breaker is None:
                _default_breaker = CircuitBreaker(
                    failure_threshold=int(
                        _env_float(THRESHOLD_ENV, DEFAULT_FAILURE_THRESHOLD, minimum=1)
                    ),
                    cooldown_seconds=_env_float(COOLDOWN_ENV, DEFAULT_COOLDOWN_SECONDS),
                )
    return _default_breaker


def reset_breaker() -> None:
    """Testler/deploy sonrası: varsayılan devre kesiciyi sıfırla."""
    global _default_breaker
    with _default_lock:
        _default_breaker = None


def llm_stats() -> dict[str, int]:
    """``scan.stats`` için anlık sayaçlar."""
    return get_breaker().snapshot()

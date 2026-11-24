"""Kanıttan güven: log-odds toplamı.

Her bağımsız kanıt (kural, kaynak) log-odds (nat) katkısı verir; aynı kaynaktan
tekrar eden kanıtlar katlanmaz (en büyük ağırlık alınır). Çelişen kanıtlar
çıkarılır. LLM skoru değiştiremez; yalnız gerekçe yazar.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

DEFAULT_PRIOR = 0.05
MAX_LOGIT = 9.0


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    x = max(-MAX_LOGIT, min(MAX_LOGIT, x))
    return 1.0 / (1.0 + math.exp(-x))


def combine(evidence: Iterable[dict[str, Any]]) -> float:
    """Bağımsız kaynak başına en büyük ağırlığı alıp toplar."""
    best: dict[tuple[str, str], float] = {}
    for entry in evidence:
        weight = float(entry.get("weight") or 0.0)
        if weight <= 0:
            continue
        key = (str(entry.get("rule") or ""), str(entry.get("source") or ""))
        best[key] = max(best.get(key, 0.0), weight)
    return sum(best.values())


def score(
    supporting: Iterable[dict[str, Any]],
    contradicting: Iterable[dict[str, Any]] = (),
    *,
    prior: float = DEFAULT_PRIOR,
) -> float:
    """Posterior olasılık = sigmoid(logit(prior) + destek - çelişki)."""
    return round(sigmoid(logit(prior) + combine(supporting) - combine(contradicting)), 4)

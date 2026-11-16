from typing import Protocol

from showis_contracts import (
    Edge,
    Entity,
    Observation,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
)
from showis_engine.http import DEFAULT_UA, EvidenceStore, HttpClient, RobotsCache


class Connector(Protocol):
    name: str
    platform: Platform
    tier: RiskTier
    supported_kinds: set[str]

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        """Hedef + seed'lerden bu connector için görev listesi üret."""
        ...

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        """Görevi çalıştır, ham gözlemler döndür. Hata -> error alanı dolu Observation."""
        ...

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        """Gözlemi Entity/Edge'lere çevir."""
        ...


__all__ = [
    "Connector",
    "HttpClient",
    "RobotsCache",
    "EvidenceStore",
    "DEFAULT_UA",
]

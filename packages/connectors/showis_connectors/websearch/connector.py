"""Isim/okul bazli web aramasi connector'i (Tier C).

Kullanici yalnizca ad-soyad (ve varsa okul/sehir) verdiginde bile sonuc
uretmek icin arama motoru sonuclarini dogrudan Entity'ye cevirir. Boylece
site-check hicbir hesap bulamasa da arama sonuclari "Bulgular" sekmesine
ipucu (lead) olarak duser; skorlamayi Resolver yapar.

Tier C kurali: yalnizca arama indeksi sorgulanir, sonuc sayfasi cekilmez.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from showis_contracts import (
    Edge,
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
)
from showis_engine.http import EvidenceStore

from .base import SearchHit, SearchProvider

_MAX_TITLE = 120

# Arama motorlarinin reklam/yonlendirme sarmalayicilari: gercek hedef URL'yi
# gostermezler, bulgu olarak degersizdir.
_WRAPPER_RE = re.compile(
    r"google\.[a-z.]+/goto|duckduckgo\.com/(?:l/|y\.js)|bing\.com/aclick|/aclk\?",
    re.IGNORECASE,
)


class WebSearchConnector:
    name: str = "websearch"
    platform: Platform = Platform.WEB
    tier: RiskTier = RiskTier.C
    supported_kinds: set[str] = {"search"}
    detection: str = "websearch"

    def __init__(
        self,
        search_provider: SearchProvider | None = None,
        evidence_store: EvidenceStore | None = None,
        limit: int = 20,
    ):
        self.search_provider = search_provider
        self.evidence_store = evidence_store or EvidenceStore()
        self.limit = limit

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def _queries(self, ctx: ScanContext) -> list[str]:
        target = ctx.target
        fullname = f"{target.first_name} {target.last_name}".strip()
        if not fullname:
            return []
        hints = target.hints or {}
        queries = [f'"{fullname}"']
        # Okul/sehir ipucu ayni isimli kisileri ayiklar.
        for key in ("school", "city", "company"):
            value = str(hints.get(key) or "").strip()
            if value:
                queries.append(f'"{fullname}" {value}')
        return queries

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        if not self.search_provider:
            return []
        return [
            TaskSpec(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind="search",
                params={"query": query, "limit": self.limit},
                priority=1,
            )
            for query in self._queries(ctx)
        ]

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        query = str(task.params.get("query") or "").strip()
        limit = int(task.params.get("limit") or self.limit)
        if not (self.search_provider and query):
            return []

        hits: list[SearchHit] = await self.search_provider.search(query, limit=limit)
        hits = [hit for hit in hits if not _WRAPPER_RE.search(hit.url)]
        evidence = []
        payload: list[dict[str, Any]] = []
        for hit in hits:
            text = f"{hit.title}\n{hit.snippet}\n{hit.url}".encode()
            ev = self.evidence_store.store_bytes(
                text, source_url=hit.url, kind="text", http_status=200
            )
            evidence.append(ev)
            payload.append(
                {
                    "url": hit.url,
                    "title": hit.title,
                    "snippet": hit.snippet,
                    "rank": hit.rank,
                    "platform": hit.platform.value,
                    "subject": hit.subject_id,
                    "evidence_id": ev.id,
                }
            )

        return [
            Observation(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind=ObservationKind.SEARCH_RESULT,
                subject=query,
                tier=self.tier,
                data={"query": query, "total": len(payload), "hits": payload},
                evidence=evidence,
            )
        ]

    # ------------------------------------------------------------------
    # Normalize
    # ------------------------------------------------------------------

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        entities: list[Entity] = []
        if obs.error or obs.blocked or not obs.data:
            return entities, []

        query = str(obs.data.get("query") or "")
        ev_ids = {ev.id: ev for ev in obs.evidence}

        for hit in obs.data.get("hits") or []:
            url = str(hit.get("url") or "")
            if not url:
                continue
            title = str(hit.get("title") or "").strip()[:_MAX_TITLE]
            entity_evidence = [
                ev.id for ev in [ev_ids.get(hit.get("evidence_id"))] if ev is not None
            ]
            entities.append(
                Entity(
                    # URL tabanli kimlik: ayni sonuc farkli sorgularda tekrar
                    # geldiginde scanner entity'leri birlestirir.
                    id=f"search:{hashlib.sha1(url.encode()).hexdigest()[:12]}",
                    type=EntityType.ACCOUNT,
                    platform=Platform.WEB,
                    label=title or url,
                    url=url,
                    attributes={
                        "name": title,
                        "search_title": title,
                        "search_snippet": str(hit.get("snippet") or "")[:500],
                        "search_rank": hit.get("rank"),
                        "search_platform": hit.get("platform"),
                        "search_subject": hit.get("subject"),
                        "search_query": query,
                        "detection": self.detection,
                    },
                    evidence_ids=entity_evidence,
                )
            )
        return entities, []

"""OpenAlex connector — anahtarsız akademik yazar keşfi.

OpenAlex, 270M+ çalışma ve 90M+ araştırmacıyı kapsayan açık bir katalogdur.
Ad-soyad hedefi için `/authors?search=` uç noktası kullanılır; dönen yazarlar
ORCID, kurum, çalışma sayısı ve atıf bilgisiyle birlikte "lead" olarak kayda
düşer. Anahtar gerekmez; günlük ücretsiz kota düşüktür, bu yüzden tarama
başına **tek** istek atılır.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote

from showis_contracts import (
    Edge,
    Entity,
    EntityType,
    Observation,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
    make_entity_id,
)

from .base import ProfileFetch, PublicProfileConnector

OPENALEX_BASE = "https://api.openalex.org"


class OpenAlexConnector(PublicProfileConnector):
    name: str = "openalex"
    platform: Platform = Platform.OPENALEX
    tier: RiskTier = RiskTier.A
    supported_kinds: set[str] = {"search_authors"}

    profile_url_template = "https://openalex.org/authors/{id}"
    identifier_pattern = r"openalex\.org/(A\d+)"

    def _seed_tasks(self, ctx: ScanContext) -> list[TaskSpec]:
        return []

    def _variant_tasks(self, ctx: ScanContext, existing: set[str]) -> list[TaskSpec]:
        return []

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        fullname = f"{ctx.target.first_name} {ctx.target.last_name}".strip()
        if not fullname:
            return []
        return [
            TaskSpec(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=self.platform,
                kind="search_authors",
                params={"query": fullname},
                priority=3,
            )
        ]

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        query = str(task.params.get("query") or "").strip()
        if not query:
            return [self._obs("", error="arama sorgusu eksik")]
        params = f"?search={quote(query, safe='')}&per_page=5"
        evidence_url = f"{OPENALEX_BASE}/authors{params}"
        mailto = os.getenv("SHOWIS_CONTACT_EMAIL", "").strip()
        if mailto:
            params += f"&mailto={quote(mailto, safe='')}"
        url = f"{OPENALEX_BASE}/authors{params}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code >= 400:
            return [
                self._obs(
                    query,
                    data={},
                    error=f"OpenAlex HTTP {resp.status_code}",
                )
            ]
        payload = resp.json()
        results = payload.get("results") or []
        if not results:
            return [self._obs(query, data={"exists": False, "authors": []})]
        ev = self.evidence_store.store_bytes(
            resp.content, source_url=evidence_url, kind="json", http_status=resp.status_code
        )
        return [
            self._obs(
                query,
                url=evidence_url,
                data={"exists": True, "query": query, "authors": results[:5]},
                evidence=[ev],
            )
        ]

    async def fetch(self, identifier: str) -> ProfileFetch | None:  # pragma: no cover
        return None

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        if obs.error or obs.blocked or not obs.data:
            return [], []
        authors = obs.data.get("authors")
        if not authors:
            return super().normalize(obs)
        ev_ids = [ev.id for ev in obs.evidence]
        entities: list[Entity] = []
        for author in authors:
            openalex_id = str(author.get("id") or "").rsplit("/", 1)[-1]
            if not openalex_id:
                continue
            orcid = str(author.get("orcid") or "").rstrip("/").rsplit("/", 1)[-1] or None
            display_name = author.get("display_name") or openalex_id
            institutions = author.get("last_known_institutions") or []
            institution_names = [
                item.get("display_name")
                for item in institutions
                if isinstance(item, dict) and item.get("display_name")
            ]
            entities.append(
                Entity(
                    id=make_entity_id(self.platform.value, openalex_id),
                    type=EntityType.ACCOUNT,
                    platform=self.platform,
                    label=str(display_name),
                    url=(
                        f"https://orcid.org/{orcid}"
                        if orcid
                        else f"https://openalex.org/authors/{openalex_id}"
                    ),
                    attributes={
                        "name": display_name,
                        "openalex_id": openalex_id,
                        "orcid": orcid,
                        "works_count": author.get("works_count"),
                        "cited_by_count": author.get("cited_by_count"),
                        "institution": institution_names[0] if institution_names else None,
                        "search_query": obs.data.get("query"),
                        "detection": "openalex_search",
                        "confidence_hint": 0.7,
                    },
                    evidence_ids=ev_ids,
                )
            )
        return entities, []

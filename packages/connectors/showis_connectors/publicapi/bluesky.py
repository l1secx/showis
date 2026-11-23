"""Bluesky connector — public.api.bsky.app kullanarak profil verisi çeker.

AT Protocol, XRPC tabanlı herkese açık profil API'si sunar. Kullanıcı
handle'ları ``{ad}.bsky.social`` ya da özel alan adı (``ornek.com``)
biçiminde olur; API'den dönen veri şunları içerir: displayName, description,
handle, did, avatar, banner, createdAt.
"""

from __future__ import annotations

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


class BlueskyConnector(PublicProfileConnector):
    name: str = "bluesky"
    platform: Platform = Platform.BLUESKY
    tier: RiskTier = RiskTier.A
    supported_kinds: set[str] = {"fetch_profile", "search_actors"}

    profile_url_template = "https://bsky.app/profile/{id}"
    identifier_pattern = r"bsky\.app/profile/([a-zA-Z0-9.-]+)"

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        """Profil varyantlarına ek olarak ad-soyad ile aktör araması."""
        tasks = await super().plan(ctx)
        fullname = f"{ctx.target.first_name} {ctx.target.last_name}".strip()
        if fullname:
            tasks.append(
                TaskSpec(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=self.platform,
                    kind="search_actors",
                    params={"query": fullname},
                    priority=3,
                )
            )
        return tasks

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        if task.kind != "search_actors":
            return await super().run(task, ctx)
        query = str(task.params.get("query") or "").strip()
        if not query:
            return [self._obs("", error="arama sorgusu eksik")]
        url = (
            "https://public.api.bsky.app/xrpc/app.bsky.actor.searchActors"
            f"?q={quote(query, safe='')}&limit=10"
        )
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
                    error=f"Bluesky searchActors HTTP {resp.status_code}",
                )
            ]
        payload = resp.json()
        actors = payload.get("actors") or []
        if not actors:
            return [self._obs(query, data={"exists": False, "actors": []})]
        ev = self.evidence_store.store_bytes(
            resp.content, source_url=url, kind="json", http_status=resp.status_code
        )
        return [
            self._obs(
                query,
                url=url,
                data={"exists": True, "query": query, "actors": actors},
                evidence=[ev],
            )
        ]

    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        if obs.error or obs.blocked or not obs.data:
            return [], []
        actors = obs.data.get("actors")
        if not actors:
            return super().normalize(obs)
        ev_ids = [ev.id for ev in obs.evidence]
        entities: list[Entity] = []
        for actor in actors:
            handle = str(actor.get("handle") or "").strip()
            if not handle:
                continue
            entities.append(
                Entity(
                    id=make_entity_id(self.platform.value, handle),
                    type=EntityType.ACCOUNT,
                    platform=self.platform,
                    label=handle,
                    url=f"https://bsky.app/profile/{handle}",
                    attributes={
                        "username": handle,
                        "display_name": actor.get("displayName"),
                        "name": actor.get("displayName"),
                        "bio": actor.get("description"),
                        "avatar_url": actor.get("avatar"),
                        "created_at": actor.get("createdAt"),
                        "did": actor.get("did"),
                        "detection": "search_actors",
                        "search_query": obs.data.get("query"),
                    },
                    evidence_ids=ev_ids,
                )
            )
        return entities, []

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        """Bluesky XRPC'den profil verisi çeker."""
        # Nokta içeren handle zaten tam addres (ad.bsky.social veya özel alan adı);
        # aksi halde varsayılan sunucu eklenir.
        handle = identifier if "." in identifier else f"{identifier}.bsky.social"

        url = f"https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={handle}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (400, 404):
            # Geçersiz handle / kullanıcı yok
            return None
        resp.raise_for_status()
        data = resp.json()
        return ProfileFetch(
            url=str(resp.url),
            data={
                "username": data.get("handle"),
                "display_name": data.get("displayName"),
                "bio": data.get("description"),
                "avatar_url": data.get("avatar"),
                "banner_url": data.get("banner"),
                "created_at": data.get("createdAt"),
                "did": data.get("did"),
            },
            raw=resp.content,
            http_status=resp.status_code,
            evidence_kind="json",
        )

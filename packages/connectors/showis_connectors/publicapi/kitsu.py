"""Kitsu connector — JSON:API tabanlı kullanıcı profil API'si.

Kitsu API, users endpoint'inden tam kullanıcı profil bilgisi çeker:
name, bio, avatar, location, website, createdAt, waifu, followersCount vb.
"""

from __future__ import annotations

from showis_contracts import Platform, RiskTier

from .base import ProfileFetch, PublicProfileConnector


class KitsuConnector(PublicProfileConnector):
    name: str = "kitsu"
    platform: Platform = Platform.KITSU
    tier: RiskTier = RiskTier.A

    profile_url_template = "https://kitsu.io/users/{id}"
    identifier_pattern = r"kitsu\.io/users/([a-zA-Z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        """Kitsu API'den kullanıcı profil verisi çeker."""
        url = f"https://kitsu.io/api/edge/users?filter[slug]={identifier}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/vnd.api+json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        users = resp.json().get("data", [])
        if not users:
            return None
        attrs = users[0].get("attributes", {})
        return ProfileFetch(
            url=str(resp.url),
            data={
                "username": attrs.get("slug"),
                "display_name": attrs.get("name"),
                "bio": attrs.get("bio"),
                "avatar_url": attrs.get("avatar"),
                "location": attrs.get("location"),
                "website": attrs.get("website"),
                "created_at": attrs.get("createdAt"),
                "waifu": attrs.get("waifu"),
                "followers_count": attrs.get("followersCount"),
            },
            raw=resp.content,
            http_status=resp.status_code,
            evidence_kind="json",
        )

"""Bluesky connector — public.api.bsky.app kullanarak profil verisi çeker.

AT Protocol, XRPC tabanlı herkese açık profil API'si sunar. Kullanıcı
handle'ları ``{ad}.bsky.social`` ya da özel alan adı (``ornek.com``)
biçiminde olur; API'den dönen veri şunları içerir: displayName, description,
handle, did, avatar, banner, createdAt.
"""

from __future__ import annotations

from showis_contracts import Platform, RiskTier

from .base import ProfileFetch, PublicProfileConnector


class BlueskyConnector(PublicProfileConnector):
    name: str = "bluesky"
    platform: Platform = Platform.BLUESKY
    tier: RiskTier = RiskTier.A

    profile_url_template = "https://bsky.app/profile/{id}"
    identifier_pattern = r"bsky\.app/profile/([a-zA-Z0-9.-]+)"

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

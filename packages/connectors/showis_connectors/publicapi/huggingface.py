"""HuggingFace connector — API ile model/dataset sahibi profili çeker.

HuggingFace public API'den author={username} ile kullanıcı profil alanları
çıkartılır: avatarUrl, name, bio, type (user/org), createdAt.
"""

from __future__ import annotations

from showis_contracts import Platform, RiskTier

from .base import ProfileFetch, PublicProfileConnector


class HuggingFaceConnector(PublicProfileConnector):
    name: str = "huggingface"
    platform: Platform = Platform.HUGGINGFACE
    tier: RiskTier = RiskTier.A

    profile_url_template = "https://huggingface.co/{id}"
    identifier_pattern = r"huggingface\.co/([a-zA-Z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        """HuggingFace API'den kullanıcı profil verisi çeker."""
        url = f"https://huggingface.co/api/users/{identifier}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return ProfileFetch(
            url=str(resp.url),
            data={
                "username": data.get("user"),
                "display_name": data.get("fullname"),
                "bio": data.get("bio"),
                "avatar_url": data.get("avatarUrl"),
                "location": data.get("location"),
                "company": data.get("company"),
                "website": data.get("website"),
                "joined_at": data.get("createdAt"),
                "type": data.get("type"),
            },
            raw=resp.content,
            http_status=resp.status_code,
            evidence_kind="json",
        )

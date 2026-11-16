"""Bitbucket connector (Tier A — public users API).

`api.bitbucket.org/2.0/users/{username}` anahtarsiz, herkese acik.
"""

from __future__ import annotations

from typing import Any

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class BitbucketConnector(PublicProfileConnector):
    name = "bitbucket"
    platform = Platform.BITBUCKET
    profile_url_template = "https://bitbucket.org/{id}/"
    identifier_pattern = r"bitbucket\.org/([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        resp = await self.http_client.get(
            f"https://api.bitbucket.org/2.0/users/{identifier}",
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (403, 404):
            # 404: hesap yok, 403: hesap gizli/erisim kapali -> "yok" say
            return None
        resp.raise_for_status()

        user: dict[str, Any] = resp.json()
        links = user.get("links") or {}
        html_link = (links.get("html") or {}).get("href")
        avatar = (links.get("avatar") or {}).get("href")

        data: dict[str, Any] = {
            "username": user.get("username") or identifier,
            "name": user.get("display_name"),
            "bio": None,
            "location": user.get("location"),
            "avatar_url": avatar,
            "created_at": user.get("created_on"),
            "profile_url": html_link or self.profile_url(identifier),
            "account_id": user.get("account_id"),
        }
        return ProfileFetch(
            url=data["profile_url"],
            data=data,
            raw=resp.content,
            http_status=resp.status_code,
        )

    def entity_attributes(self, data: dict[str, Any], identifier: str) -> dict[str, Any]:
        attrs = super().entity_attributes(data, identifier)
        for key in ("account_id",):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return attrs

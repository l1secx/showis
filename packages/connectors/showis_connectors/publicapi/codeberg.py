"""Codeberg connector (Tier A — Gitea public users API).

`codeberg.org/api/v1/users/{username}` anahtarsiz, herkese acik.
"""

from __future__ import annotations

from typing import Any

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class CodebergConnector(PublicProfileConnector):
    name = "codeberg"
    platform = Platform.CODEBERG
    profile_url_template = "https://codeberg.org/{id}"
    identifier_pattern = r"codeberg\.org/([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        resp = await self.http_client.get(
            f"https://codeberg.org/api/v1/users/{identifier}",
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()

        user: dict[str, Any] = resp.json()
        if not user or not user.get("login"):
            return None

        login = user.get("login") or identifier
        data: dict[str, Any] = {
            "username": login,
            "name": user.get("full_name") or login,
            "bio": user.get("description") or None,
            "location": user.get("location"),
            "avatar_url": user.get("avatar_url"),
            "created_at": user.get("created"),
            "followers_count": user.get("followers_count"),
            "following_count": user.get("following_count"),
            "profile_url": user.get("html_url") or self.profile_url(login),
            "codeberg_id": user.get("id"),
            "website": user.get("website"),
        }
        links: list[str] = []
        if data["website"]:
            links.append(str(data["website"]))
        if links:
            data["links"] = links

        return ProfileFetch(
            url=data["profile_url"],
            data=data,
            raw=resp.content,
            http_status=resp.status_code,
        )

    def entity_attributes(self, data: dict[str, Any], identifier: str) -> dict[str, Any]:
        attrs = super().entity_attributes(data, identifier)
        for key in ("followers_count", "following_count", "codeberg_id", "website"):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return attrs

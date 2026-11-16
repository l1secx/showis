"""Codeforces connector (Tier A — public user API)."""

from __future__ import annotations

import logging
from typing import Any

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector

logger = logging.getLogger("showis.publicapi.codeforces")


class CodeforcesConnector(PublicProfileConnector):
    name = "codeforces"
    platform = Platform.CODEFORCES
    profile_url_template = "https://codeforces.com/profile/{id}"
    identifier_pattern = r"(?:www\.)?codeforces\.com/profile/([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://codeforces.com/api/user.info?handles={identifier}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (404, 400):
            return None
        resp.raise_for_status()

        body: dict[str, Any] = resp.json()
        if body.get("status") != "OK":
            return None

        result = body.get("result", [])
        if not result or not isinstance(result, list):
            return None

        u = result[0]
        first_name = u.get("firstName") or ""
        last_name = u.get("lastName") or ""
        fullname = f"{first_name} {last_name}".strip()

        location_parts = [part for part in [u.get("city"), u.get("country")] if part]
        location = ", ".join(location_parts) if location_parts else None

        avatar_url = u.get("avatar") or u.get("titlePhoto")
        if avatar_url and avatar_url.startswith("//"):
            avatar_url = f"https:{avatar_url}"

        return ProfileFetch(
            url=self.profile_url(identifier),
            data={
                "name": fullname or u.get("handle", identifier),
                "username": u.get("handle", identifier),
                "location": location,
                "company": u.get("organization"),
                "avatar_url": avatar_url,
                "rating": u.get("rating"),
                "rank": u.get("rank"),
                "max_rating": u.get("maxRating"),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

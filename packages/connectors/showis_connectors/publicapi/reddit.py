"""Reddit connector (Tier A — public user API)."""

from __future__ import annotations

import logging

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector

logger = logging.getLogger("showis.publicapi.reddit")


class RedditConnector(PublicProfileConnector):
    name = "reddit"
    platform = Platform.REDDIT
    profile_url_template = "https://www.reddit.com/user/{id}"
    identifier_pattern = r"(?:www\.|old\.)?reddit\.com/user/([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://www.reddit.com/user/{identifier}/about.json"
        # Reddit requires a custom User-Agent to avoid 429
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Showis-OSINT/1.0",
            "Accept": "application/json",
        }
        resp = await self.http_client.get(
            url,
            headers=headers,
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (404, 403):
            return None
        resp.raise_for_status()

        data = resp.json()
        if not isinstance(data, dict):
            return None
        if data.get("error") == 404:
            return None

        user_data = data.get("data")
        if not user_data or not isinstance(user_data, dict):
            return None

        if user_data.get("is_suspended"):
            return None

        sub = user_data.get("subreddit") or {}
        display_name = sub.get("title") or user_data.get("name")
        bio = sub.get("public_description") or ""
        avatar_url = user_data.get("icon_img")
        # Clean Reddit avatar URL query params if any
        if avatar_url and "?" in avatar_url:
            avatar_url = avatar_url.split("?")[0]

        total_karma = user_data.get("total_karma", 0)

        return ProfileFetch(
            url=self.profile_url(identifier),
            data={
                "name": display_name,
                "username": user_data.get("name", identifier),
                "bio": bio,
                "avatar_url": avatar_url,
                "created_at": user_data.get("created_utc"),
                "total_karma": total_karma,
                "verified": user_data.get("verified", False),
                "is_gold": user_data.get("is_gold", False),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

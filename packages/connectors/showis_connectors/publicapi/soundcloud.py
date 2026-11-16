"""SoundCloud connector (Tier B — public profile page meta)."""

from __future__ import annotations

import logging

from showis_contracts import Platform, RiskTier
from showis_engine.htmlutil import meta_content, og_image

from .base import ProfileFetch, PublicProfileConnector

logger = logging.getLogger("showis.publicapi.soundcloud")


class SoundCloudConnector(PublicProfileConnector):
    name = "soundcloud"
    platform = Platform.SOUNDCLOUD
    tier = RiskTier.B
    profile_url_template = "https://soundcloud.com/{id}"
    identifier_pattern = r"(?:www\.)?soundcloud\.com/([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = self.profile_url(identifier)
        resp = await self.http_client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Showis-OSINT/1.0"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (404, 403, 410):
            return None
        resp.raise_for_status()

        html_text = resp.text
        title = meta_content(html_text, prop="og:title")
        description = meta_content(html_text, prop="og:description")
        avatar = og_image(html_text)

        # "SoundCloud" generic page check
        generic_titles = ("soundcloud", "stream and listen to music online")
        if not title or title.strip().lower() in generic_titles:
            return None

        # SoundCloud og:title format: "Name | Listen to music tracks..."
        clean_name = title.split("|")[0].strip() if "|" in title else title.strip()

        return ProfileFetch(
            url=url,
            data={
                "name": clean_name or identifier,
                "username": identifier,
                "bio": description or "",
                "avatar_url": avatar,
            },
            raw=resp.content,
            http_status=resp.status_code,
            evidence_kind="html",
        )

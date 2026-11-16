"""Medium connector (Tier B — public RSS profile feed)."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector

logger = logging.getLogger("showis.publicapi.medium")


class MediumConnector(PublicProfileConnector):
    name = "medium"
    platform = Platform.MEDIUM
    profile_url_template = "https://medium.com/@{id}"
    identifier_pattern = r"(?:www\.)?medium\.com/@([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        clean_id = identifier.lstrip("@")
        url = f"https://medium.com/feed/@{clean_id}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (404, 403, 410):
            return None
        resp.raise_for_status()

        try:
            root = ET.fromstring(resp.content)
            channel = root.find("channel")
            if channel is None:
                return None

            title_elem = channel.find("title")
            raw_title = ""
            if title_elem is not None and title_elem.text:
                raw_title = title_elem.text.strip()
            # Medium format: "Stories by {Name} on Medium"
            name_match = re.search(r"Stories by (.+?) on Medium", raw_title, re.IGNORECASE)
            name = name_match.group(1).strip() if name_match else raw_title

            desc_elem = channel.find("description")
            bio = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""

            image_elem = channel.find("image")
            avatar_url = None
            if image_elem is not None:
                url_elem = image_elem.find("url")
                if url_elem is not None and url_elem.text:
                    avatar_url = url_elem.text.strip()

            return ProfileFetch(
                url=self.profile_url(clean_id),
                data={
                    "name": name,
                    "username": clean_id,
                    "bio": bio,
                    "avatar_url": avatar_url,
                },
                raw=resp.content,
                http_status=resp.status_code,
                evidence_kind="xml",
            )
        except Exception as exc:
            logger.debug("Medium XML ayrıştırılamadı (%s): %s", identifier, exc)
            return None

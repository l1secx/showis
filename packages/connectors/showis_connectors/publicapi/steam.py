"""Steam connector (Tier B — steamcommunity.com acik profil sayfasi).

Custom URL deseni: https://steamcommunity.com/id/{customurl}
Sayfa oturumsuz erisilebilir; hesap yoksa Steam hata sayfasi dondurur
("profile could not be found") — bu isaretle yok sayilir.
"""

from __future__ import annotations

import re
from typing import Any

from showis_contracts import Platform, RiskTier
from showis_engine.htmlutil import og_image, og_title, strip_html

from .base import ProfileFetch, PublicProfileConnector

_NOT_FOUND_MARKERS = (
    b"profile could not be found",
    b"The specified profile could not be found",
)


class SteamConnector(PublicProfileConnector):
    name = "steam"
    platform = Platform.STEAM
    tier = RiskTier.B
    profile_url_template = "https://steamcommunity.com/id/{id}"
    identifier_pattern = r"steamcommunity\.com/(?:id|profiles)/([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = self.profile_url(identifier)
        resp = await self.http_client.get(url, check_robots=True)
        body = resp.content
        lowered = body.lower()
        if resp.status_code == 404 or any(m.lower() in lowered for m in _NOT_FOUND_MARKERS):
            return None
        if resp.status_code != 200:
            resp.raise_for_status()

        html = resp.text
        title = og_title(html) or ""
        # "PersonaName :: Steam Community" / "Steam Community :: PersonaName"
        parts = [p.strip() for p in title.split("::")]
        display_name = (
            next((p for p in parts if p and p.lower() != "steam community"), "")
            or identifier
        )
        text = strip_html(html)

        data: dict[str, Any] = {
            "name": display_name,
            "username": identifier,
            "avatar_url": og_image(html),
            "profile_url": url,
            "text_excerpt": text[:2000],
        }
        # Steam profil metninden basit alanlar (muhafazakar regex)
        m = re.search(r"Member since\s+([^<\n]+)", text)
        if m:
            data["member_since"] = m.group(1).strip()
        m = re.search(r"(\d[\d,]*)\s+BADGES?\b", text, re.IGNORECASE)
        if m:
            data["badge_count"] = m.group(1)

        return ProfileFetch(
            url=url,
            data=data,
            raw=body[:16384],
            http_status=resp.status_code,
            evidence_kind="html",
        )

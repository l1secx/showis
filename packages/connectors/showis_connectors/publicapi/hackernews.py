"""Hacker News connector (Tier B — herkese acik kullanici sayfasi).

news.ycombinator.com/user?id={name} basit HTML tablo dondurur:
karma, hesap acilis tarihi ve about metni. "No such user." metni
hesap olmadigini gosterir.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from showis_contracts import Platform, RiskTier
from showis_engine.htmlutil import strip_html

from .base import ProfileFetch, PublicProfileConnector


def _cell(text: str, label: str) -> str | None:
    m = re.search(
        rf"{re.escape(label)}:</td>\s*<td[^>]*>(.*?)</td>", text, re.IGNORECASE | re.DOTALL
    )
    if not m:
        return None
    return strip_html(m.group(1)).strip() or None


class HackerNewsConnector(PublicProfileConnector):
    name = "hackernews"
    platform = Platform.HACKERNEWS
    tier = RiskTier.B
    profile_url_template = "https://news.ycombinator.com/user?id={id}"
    identifier_pattern = r"news\.ycombinator\.com/user\?id=([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://news.ycombinator.com/user?id={identifier}"
        resp = await self.http_client.get(url, check_robots=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        html = resp.text

        if "No such user" in html:
            return None

        karma_raw = _cell(html, "karma")
        karma = None
        if karma_raw:
            m = re.search(r"([\d,]+)", karma_raw)
            if m:
                try:
                    karma = int(m.group(1).replace(",", ""))
                except ValueError:
                    karma = None

        data: dict[str, Any] = {
            "name": identifier,
            "username": identifier,
            "created_at": _cell(html, "created"),
            "karma": karma,
            "about": _cell(html, "about"),
        }
        # Anahtarsız HN Algolia API'si: son gönderi/yorum etkinliği. Profil
        # sayfası çekilemezse bile bu ikinci kaynak kanıt kalitesini artırır.
        try:
            activity_url = (
                "https://hn.algolia.com/api/v1/search"
                f"?tags=author_{quote(identifier, safe='')}&hitsPerPage=10"
            )
            activity_resp = await self.http_client.get(
                activity_url,
                headers={"Accept": "application/json"},
                check_robots=False,
                follow_redirects=True,
            )
            if activity_resp.status_code == 200:
                payload = activity_resp.json()
                hits = payload.get("hits") or []
                data["activity_count"] = int(payload.get("nbHits") or 0)
                data["latest_activity"] = hits[0].get("created_at") if hits else None
                data["recent_items"] = [
                    {
                        "title": hit.get("title") or hit.get("story_title"),
                        "created_at": hit.get("created_at"),
                        "object_id": hit.get("objectID"),
                    }
                    for hit in hits[:5]
                ]
        except Exception:  # pragma: no cover - opsiyonel zenginleştirme
            pass
        return ProfileFetch(
            url=url,
            data=data,
            raw=resp.content[:16384],
            http_status=resp.status_code,
            evidence_kind="html",
        )

"""Chess.com connector (Tier A — public player API).

https://api.chess.com/pub/player/{username} herkese acik JSON dondurur.
"""

from __future__ import annotations

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class ChessConnector(PublicProfileConnector):
    name = "chess"
    platform = Platform.CHESS
    profile_url_template = "https://www.chess.com/member/{id}"
    identifier_pattern = r"chess\.com/(?:member|player)/([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://api.chess.com/pub/player/{identifier}"
        resp = await self.http_client.get(
            url, headers={"Accept": "application/json"}, check_robots=False, follow_redirects=True
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("username"):
            return None

        return ProfileFetch(
            url=payload.get("url") or self.profile_url(identifier),
            data={
                "name": payload.get("name"),
                "username": payload.get("username") or identifier,
                "title": payload.get("title"),
                "status": payload.get("status"),
                "country": payload.get("country"),
                "location": payload.get("location"),
                "avatar_url": payload.get("avatar"),
                "joined": payload.get("joined"),
                "last_online": payload.get("last_online"),
                "followers": payload.get("followers"),
                "is_streamer": payload.get("is_streamer"),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

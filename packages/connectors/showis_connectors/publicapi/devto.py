"""dev.to connector (Tier A — public users API).

https://dev.to/api/users/by_username?url={name} oturumsuz JSON dondurur
ve Twitter/GitHub baglantilari dahil zengin profil verir.
"""

from __future__ import annotations

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class DevtoConnector(PublicProfileConnector):
    name = "devto"
    platform = Platform.DEVTO
    profile_url_template = "https://dev.to/{id}"
    identifier_pattern = r"(?:dev\.to|developers\.com)/([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://dev.to/api/users/by_username?url={identifier}"
        resp = await self.http_client.get(
            url, headers={"Accept": "application/json"}, check_robots=False, follow_redirects=True
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("username"):
            return None

        username = payload.get("username") or identifier
        github = payload.get("github_username")
        twitter = payload.get("twitter_username")
        website = payload.get("website_url")

        links: list[str] = [u for u in [website] if u]
        social: dict[str, str] = {}
        if github:
            social["github"] = github
            links.append(f"https://github.com/{github}")
        if twitter:
            social["twitter"] = twitter
            links.append(f"https://x.com/{twitter}")

        return ProfileFetch(
            url=f"https://dev.to/{username}",
            data={
                "name": payload.get("name"),
                "username": username,
                "bio": payload.get("summary"),
                "location": payload.get("location"),
                "avatar_url": payload.get("profile_image"),
                "created_at": payload.get("joined_at"),
                "github_link": f"https://github.com/{github}" if github else None,
                "twitter_link": f"https://x.com/{twitter}" if twitter else None,
                "website": website,
                "social": social,
                "links": links,
                "article_count": payload.get("articles_count"),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

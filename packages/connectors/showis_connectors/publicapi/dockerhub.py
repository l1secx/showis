"""Docker Hub connector (Tier A — public v2 users API)."""

from __future__ import annotations

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class DockerHubConnector(PublicProfileConnector):
    name = "dockerhub"
    platform = Platform.DOCKERHUB
    profile_url_template = "https://hub.docker.com/u/{id}"
    identifier_pattern = r"hub\.docker\.com/u/([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://hub.docker.com/v2/users/{identifier}/"
        resp = await self.http_client.get(
            url, headers={"Accept": "application/json"}, check_robots=False, follow_redirects=True
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("username") and not payload.get("full_name"):
            return None

        return ProfileFetch(
            url=payload.get("profile_url")
            or payload.get("url")
            or self.profile_url(identifier),
            data={
                "name": payload.get("full_name"),
                "username": payload.get("username") or identifier,
                "bio": payload.get("bio") or payload.get("about"),
                "location": payload.get("location"),
                "company": payload.get("company"),
                "avatar_url": payload.get("avatar_url"),
                "created_at": payload.get("date_joined"),
                "gravatar_email": payload.get("gravatar_email"),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

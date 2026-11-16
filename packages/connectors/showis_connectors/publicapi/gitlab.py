"""GitLab connector (Tier A — public users API)."""

from __future__ import annotations

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class GitLabConnector(PublicProfileConnector):
    name = "gitlab"
    platform = Platform.GITLAB
    profile_url_template = "https://gitlab.com/{id}"
    identifier_pattern = r"gitlab\.com/([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = f"https://gitlab.com/api/v4/users?username={identifier}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        users = resp.json()
        if not users:
            return None
        u = users[0]
        return ProfileFetch(
            url=u.get("web_url") or self.profile_url(identifier),
            data={
                "name": u.get("name"),
                "username": u.get("username"),
                "bio": u.get("bio"),
                "location": u.get("location"),
                "avatar_url": u.get("avatar_url"),
                "public_email": u.get("public_email"),
                "created_at": u.get("created_at"),
                "state": u.get("state"),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

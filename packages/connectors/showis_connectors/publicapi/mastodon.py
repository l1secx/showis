"""Mastodon connector (Tier A — mastodon.social public account API).

`/api/v1/accounts/lookup` anahtarsiz; `note` alani HTML bio dondurur
(`strip_html` ile duz metne cevrilir).
"""

from __future__ import annotations

from typing import Any

from showis_contracts import Platform
from showis_engine.htmlutil import strip_html

from .base import ProfileFetch, PublicProfileConnector


class MastodonConnector(PublicProfileConnector):
    name = "mastodon"
    platform = Platform.MASTODON
    profile_url_template = "https://mastodon.social/@{id}"
    identifier_pattern = r"mastodon\.social/@([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        # Federasyon adresi (@user@instance) de gonderilebilir; lookup acct kabul eder
        acct = identifier.strip().lstrip("@")
        resp = await self.http_client.get(
            "https://mastodon.social/api/v1/accounts/lookup",
            params={"acct": acct},
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (401, 403, 404):
            return None
        resp.raise_for_status()

        account: dict[str, Any] = resp.json()
        if not account or not account.get("id"):
            return None

        username = account.get("username") or acct
        note_html = account.get("note") or ""
        data: dict[str, Any] = {
            "username": username,
            "name": account.get("display_name") or username,
            "bio": strip_html(note_html) or None,
            "location": None,
            "avatar_url": account.get("avatar") or account.get("avatar_static"),
            "created_at": account.get("created_at"),
            "followers_count": account.get("followers_count"),
            "following_count": account.get("following_count"),
            "statuses_count": account.get("statuses_count"),
            "profile_url": account.get("url") or self.profile_url(username),
            "mastodon_id": str(account.get("id")),
            "bot": account.get("bot"),
            "locked": account.get("locked"),
        }
        return ProfileFetch(
            url=data["profile_url"],
            data=data,
            raw=resp.content,
            http_status=resp.status_code,
        )

    def entity_attributes(self, data: dict[str, Any], identifier: str) -> dict[str, Any]:
        attrs = super().entity_attributes(data, identifier)
        for key in (
            "followers_count",
            "following_count",
            "statuses_count",
            "mastodon_id",
            "bot",
            "locked",
        ):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return attrs

    def _task_identifier(self, task) -> str:  # noqa: ANN001 - base imzasi
        """`@user` ve `user@instance` girdilerini tutarli anahtara indirger."""
        raw = super()._task_identifier(task).lstrip("@")
        return raw

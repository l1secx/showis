"""LeetCode connector (Tier A — public GraphQL API)."""

from __future__ import annotations

import logging
from typing import Any

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector

logger = logging.getLogger("showis.publicapi.leetcode")

_LEETCODE_QUERY = """
query getUserProfile($username: String!) {
  matchedUser(username: $username) {
    username
    githubUrl
    twitterUrl
    linkedinUrl
    profile {
      realName
      aboutMe
      userAvatar
      ranking
      countryName
      skillTags
    }
  }
}
"""


class LeetCodeConnector(PublicProfileConnector):
    name = "leetcode"
    platform = Platform.LEETCODE
    profile_url_template = "https://leetcode.com/u/{id}"
    identifier_pattern = r"(?:www\.)?leetcode\.com/(?:u/)?([A-Za-z0-9_-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = "https://leetcode.com/graphql"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Showis-OSINT/1.0",
        }
        payload = {
            "query": _LEETCODE_QUERY,
            "variables": {"username": identifier},
        }

        resp = await self.http_client.post(
            url,
            json=payload,
            headers=headers,
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None

        body: dict[str, Any] = resp.json()
        data = body.get("data", {})
        matched_user = data.get("matchedUser")
        if not matched_user or not isinstance(matched_user, dict):
            return None

        profile = matched_user.get("profile") or {}
        real_name = profile.get("realName")
        about = profile.get("aboutMe")
        avatar = profile.get("userAvatar")
        country = profile.get("countryName")

        # Collect social links
        links = []
        github_link = matched_user.get("githubUrl")
        twitter_link = matched_user.get("twitterUrl")
        linkedin_link = matched_user.get("linkedinUrl")
        for link in (github_link, twitter_link, linkedin_link):
            if link:
                links.append(link)

        return ProfileFetch(
            url=self.profile_url(identifier),
            data={
                "name": real_name or identifier,
                "username": matched_user.get("username", identifier),
                "bio": about,
                "avatar_url": avatar,
                "location": country,
                "github_link": github_link,
                "twitter_link": twitter_link,
                "linkedin": linkedin_link,
                "links": links,
                "ranking": profile.get("ranking"),
                "skills": profile.get("skillTags", []),
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

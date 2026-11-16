"""WordPress.com connector — public API ile blog varlığını çeker.

WordPress.com REST API'den site bilgisi: name, description, URL, jetpack
aktifliği, posting aktivitesi vb. Kullanıcı profili değil; blog varlığı.
"""

from __future__ import annotations

from showis_contracts import Platform, RiskTier

from .base import ProfileFetch, PublicProfileConnector


class WordPressConnector(PublicProfileConnector):
    name: str = "wordpress"
    platform: Platform = Platform.WORDPRESS
    tier: RiskTier = RiskTier.A

    profile_url_template = "https://{id}.wordpress.com"
    identifier_pattern = r"([a-zA-Z0-9_-]+)\.wordpress\.com"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        """WordPress.com REST API'den site bilgisini çeker."""
        site = identifier if identifier.endswith(".wordpress.com") else (
            f"{identifier}.wordpress.com"
        )
        url = f"https://public-api.wordpress.com/rest/v1.1/sites/{site}"
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return None
        # 403/429 = erişim engeli/hız limiti: "yok" değil, hata olarak yükselir
        resp.raise_for_status()
        data = resp.json()
        return ProfileFetch(
            url=str(resp.url),
            data={
                "username": site.removesuffix(".wordpress.com"),
                "display_name": data.get("name"),
                "bio": data.get("description"),
                "url": data.get("URL"),
                "is_private": data.get("is_private"),
                "is_jetpack": data.get("is_jetpack"),
                "post_count": data.get("post_count"),
                "subscribers_count": data.get("subscribers_count"),
            },
            raw=resp.content,
            http_status=resp.status_code,
            evidence_kind="json",
        )

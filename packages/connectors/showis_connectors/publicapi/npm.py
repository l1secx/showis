"""npm connector (Tier A — public registry search API).

https://registry.npmjs.org/-/v1/search?text=maintainer:{user} herkese
acik npm registry aramasidir; total>0 ise kullanici en az bir paketin
maintainer'idir.
"""

from __future__ import annotations

from urllib.parse import quote

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector


class NpmConnector(PublicProfileConnector):
    name = "npm"
    platform = Platform.NPM
    profile_url_template = "https://www.npmjs.com/~{id}"
    identifier_pattern = r"npmjs\.com/~([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = (
            "https://registry.npmjs.org/-/v1/search"
            f"?text=maintainer:{quote(identifier)}&size=10"
        )
        resp = await self.http_client.get(
            url, headers={"Accept": "application/json"}, check_robots=False, follow_redirects=True
        )
        resp.raise_for_status()
        payload = resp.json()
        total = int(payload.get("total") or 0)
        if total <= 0:
            return None

        packages = []
        for obj in (payload.get("objects") or [])[:10]:
            pkg = obj.get("package") or {}
            packages.append(
                {
                    "name": pkg.get("name"),
                    "version": pkg.get("version"),
                    "description": (pkg.get("description") or "")[:200],
                    "date": pkg.get("date"),
                }
            )

        return ProfileFetch(
            url=self.profile_url(identifier),
            data={
                "username": identifier,
                "package_count": total,
                "packages": packages,
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

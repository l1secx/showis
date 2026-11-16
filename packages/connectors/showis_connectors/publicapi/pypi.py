"""PyPI connector (Tier B — pypi.org/user/{name}/ profil sayfasi).

Profil sayfasi sunulan projelerin linklerini icerir; proje sayisi
regex ile sayilir. Hesap yoksa PyPI 404 doner.
"""

from __future__ import annotations

import re
from typing import Any

from showis_contracts import Platform, RiskTier
from showis_engine.htmlutil import strip_html

from .base import ProfileFetch, PublicProfileConnector

_PROJECT_LINK_RE = re.compile(r'href="/project/([A-Za-z0-9_.-]+)/?"')


class PypiConnector(PublicProfileConnector):
    name = "pypi"
    platform = Platform.PYPI
    tier = RiskTier.B
    profile_url_template = "https://pypi.org/user/{id}/"
    identifier_pattern = r"pypi\.org/user/([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = self.profile_url(identifier)
        resp = await self.http_client.get(url, check_robots=True)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        html = resp.text

        projects = sorted(set(_PROJECT_LINK_RE.findall(html)))

        text = strip_html(html)
        data: dict[str, Any] = {
            "username": identifier,
            "project_count": len(projects),
            "projects": projects[:50],
            "text_excerpt": text[:1500],
        }
        return ProfileFetch(
            url=url,
            data=data,
            raw=resp.content[:16384],
            http_status=resp.status_code,
            evidence_kind="html",
        )

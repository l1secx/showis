"""Keybase connector (Tier A — public proof graphi).

keybase.io/{user}/json herkese acik JSON dondurur ve kullanicinin
GitHub/Twitter/Instagram/Reddit/HackerNews gibi platformlardaki
kanitlanmis baglantilarini icerir — capraz-platform zinciri icin
en degerli kaynaklardan biridir.
"""

from __future__ import annotations

from typing import Any

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector

#: proof_type -> entity attribute link alani
_PROOF_LINK_FIELDS = {
    "github": "github_link",
    "twitter": "twitter_link",
    "hackernews": "hackernews_link",
    "reddit": "reddit_link",
    "instagram": "instagram_link",
    "medium": "medium_link",
    "keybase": None,
}


def _proof_url(proof_type: str, nametag: str) -> str:
    base = {
        "github": "https://github.com/",
        "twitter": "https://x.com/",
        "hackernews": "https://news.ycombinator.com/user?id=",
        "reddit": "https://www.reddit.com/user/",
        "instagram": "https://www.instagram.com/",
        "medium": "https://medium.com/@",
    }.get(proof_type)
    return f"{base}{nametag}" if base else ""


class KeybaseConnector(PublicProfileConnector):
    name = "keybase"
    platform = Platform.KEYBASE
    profile_url_template = "https://keybase.io/{id}"
    identifier_pattern = r"keybase\.io/([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        url = (
            "https://keybase.io/_/api/1.0/user/lookup.json"
            f"?username={identifier}"
        )
        resp = await self.http_client.get(
            url,
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        if (payload.get("status") or {}).get("code") not in (0, None):
            return None
        them = payload.get("them")
        if isinstance(them, dict):
            them = [them]
        if not them:
            return None
        profile = them[0]
        basics = profile.get("basics") or {}
        proofs = profile.get("proofs_human") or []

        social: dict[str, str] = {}
        links: list[str] = []
        for proof in proofs:
            ptype = proof.get("proof_type")
            nametag = proof.get("nametag")
            if not ptype or not nametag:
                continue
            social[ptype] = nametag
            # Kanitlanmis profil URL'si varsa onu tercih et
            link = (
                proof.get("proof_url")
                if _PROOF_LINK_FIELDS.get(ptype) and proof.get("proof_url")
                else _proof_url(ptype, nametag)
            )
            if link:
                links.append(link)

        data: dict[str, Any] = {
            "name": basics.get("username"),
            "username": basics.get("username"),
            "bio": (profile.get("profile") or {}).get("bio"),
            "location": (profile.get("profile") or {}).get("location"),
            "created_at": basics.get("ctime"),
            "social": social,
            "links": links,
            "proofs": [
                {"type": p.get("proof_type"), "nametag": p.get("nametag")}
                for p in proofs
                if p.get("proof_type") and p.get("nametag")
            ],
        }
        # Kanitlanmis sosyal baglantilari cross-link tespiti icin duz alanlara tasi
        for ptype, field_name in _PROOF_LINK_FIELDS.items():
            if field_name and social.get(ptype):
                data[field_name] = _proof_url(ptype, social[ptype])

        return ProfileFetch(
            url=self.profile_url(basics.get("username") or identifier),
            data=data,
            raw=resp.content,
            http_status=resp.status_code,
        )

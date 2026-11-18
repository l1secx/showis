"""Keybase connector (Tier A — public proof graphi).

keybase.io/{user}/json herkese acik JSON dondurur ve kullanicinin
GitHub/Twitter/Instagram/Reddit/HackerNews gibi platformlardaki
kanitlanmis baglantilarini icerir — capraz-platform zinciri icin
en degerli kaynaklardan biridir.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any
from urllib.parse import quote

from showis_contracts import Observation, Platform, ScanContext, TaskSpec

from .base import ProfileFetch, PublicProfileConnector

#: Keybase `user/lookup` API'si hangi parametreyle sorgulandigini tutar.
#: `run()` gorevi turune gore ayarlar; `fetch()` imzasi taban sinifla uyumlu
#: kalsin diye modul duzeyinde bir contextvar kullanilir (es zamanli gorevler
#: kendi baglamlarini gorur).
_LOOKUP_PARAM: contextvars.ContextVar[str] = contextvars.ContextVar(
    "keybase_lookup_param", default="username"
)

#: API'nin dondurmesini istedigimiz alanlar (payload kucuk kalsin).
_LOOKUP_FIELDS = "basics,profile,proofs_summary"

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


def _proofs(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Kanit listesini iki yanit seklinden de cikarir.

    `proofs_human` (varsayilan yanit) ya da `fields=...proofs_summary` ile gelen
    `proofs_summary.all`. Ikisi de ayni `proof_type`/`nametag` alanlarini tasir.
    """
    proofs = profile.get("proofs_human")
    if not isinstance(proofs, list):
        summary = profile.get("proofs_summary")
        proofs = summary.get("all") if isinstance(summary, dict) else None
    return [p for p in (proofs or []) if isinstance(p, dict)]


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
    #: `profile_by_github` = D9 pivotu (e-posta→commit→login→Keybase proof).
    supported_kinds = {"fetch_profile", "profile_by_github"}

    # ------------------------------------------------------------------
    # Plan: GitHub kullanici adi pivotu (D9)
    # ------------------------------------------------------------------

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks = await super().plan(ctx)
        seen = {str(t.params.get("username") or "") for t in tasks}
        for seed in ctx.seeds:
            # Keybase `user/lookup` e-posta kabul etmiyor (D9); GitHub login'i
            # ile arama yapilir: e-posta→commit→login→Keybase zinciri.
            if seed.platform != Platform.GITHUB or not seed.username:
                continue
            login = str(seed.username).strip().lstrip("@")
            if not login or f"github:{login}" in seen:
                continue
            seen.add(f"github:{login}")
            tasks.append(
                TaskSpec(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=self.platform,
                    kind="profile_by_github",
                    params={"github": login},
                    priority=3,
                )
            )
        return tasks

    # ------------------------------------------------------------------
    # Run: gorev turune gore lookup parametresi
    # ------------------------------------------------------------------

    def _task_identifier(self, task: TaskSpec) -> str:
        if task.kind == "profile_by_github":
            return str(task.params.get("github") or "").strip().lstrip("@")
        return super()._task_identifier(task)

    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        if task.kind == "profile_by_github":
            token = _LOOKUP_PARAM.set("github")
            try:
                return await super().run(task, ctx)
            finally:
                _LOOKUP_PARAM.reset(token)
        return await super().run(task, ctx)

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        param = _LOOKUP_PARAM.get()
        url = (
            "https://keybase.io/_/api/1.0/user/lookup.json"
            f"?{param}={quote(identifier, safe='')}&fields={_LOOKUP_FIELDS}"
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
        proofs = _proofs(profile)

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

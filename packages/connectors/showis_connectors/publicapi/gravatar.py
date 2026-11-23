"""Gravatar connector (Tier A — public profile JSON API).

E-posta seed'lerinden md5 hash uretilir ve
https://en.gravatar.com/{hash}.json uzerinden herkese acik profil
verisi cekilir. JSON icindeki `accounts` alani GitHub/Twitter gibi
platform baglantilarini da icerir — guclu capraz-link kaynagidir.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from showis_contracts import Platform, ScanContext, TaskSpec

from .base import ProfileFetch, PublicProfileConnector


def gravatar_hash(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode("utf-8")).hexdigest()


class GravatarConnector(PublicProfileConnector):
    name = "gravatar"
    platform = Platform.GRAVATAR
    profile_url_template = "https://gravatar.com/{id}"

    # ------------------------------------------------------------------
    # E-posta bazli planlama — varyantlar uygulanmaz
    # ------------------------------------------------------------------

    def _seed_tasks(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks: list[TaskSpec] = []
        seen: set[str] = set()
        # Seed e-postaları + hedef ipucundaki e-posta (varsa). E-posta, Gravatar
        # için kullanıcı adından daha güçlü bir kimlik anahtarıdır.
        candidates = [seed.email for seed in ctx.seeds]
        if ctx.target.email:
            candidates.append(ctx.target.email)
        hint_email = (ctx.target.hints or {}).get("email")
        if hint_email:
            candidates.append(str(hint_email))
        for email in candidates:
            email = str(email or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            tasks.append(
                TaskSpec(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=self.platform,
                    kind="fetch_profile",
                    params={
                        "email": email,
                        "hash": gravatar_hash(email),
                    },
                    priority=1,
                )
            )
        return tasks

    def _variant_tasks(self, ctx: ScanContext, existing: set[str]) -> list[TaskSpec]:
        return []

    def _task_identifier(self, task: TaskSpec) -> str:
        return str(task.params.get("hash") or "")

    async def run(self, task: TaskSpec, ctx: ScanContext):  # type: ignore[override]
        obs_list = await super().run(task, ctx)
        email = task.params.get("email")
        for obs in obs_list:
            if email:
                # subject = gorunur kimlik; e-posta sadece veri olarak tasinir
                obs.data["email"] = email
        return obs_list

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        # identifier = md5(email)
        url = f"https://en.gravatar.com/{identifier}.json"
        resp = await self.http_client.get(
            url, headers={"Accept": "application/json"}, check_robots=False, follow_redirects=True
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        entry = (payload.get("entry") or [None])[0]
        if not entry:
            return None

        accounts = entry.get("accounts") or []
        social: dict[str, str] = {}
        links: list[str] = []
        for acc in accounts:
            domain = acc.get("domain")
            short = acc.get("shortname")
            username = acc.get("username")
            acc_url = acc.get("url")
            if username:
                social[short or domain or "account"] = username
            if acc_url:
                links.append(acc_url)

        data: dict[str, Any] = {
            "name": entry.get("displayName"),
            "bio": entry.get("aboutMe"),
            "location": entry.get("currentLocation"),
            "avatar_url": entry.get("thumbnailUrl"),
            "profile_url": entry.get("profileUrl"),
            "accounts": accounts,
            "social": social,
            "links": links,
            "emails": [
                e.get("value") for e in entry.get("emails") or [] if e.get("value")
            ],
        }
        # Cross-link icin bilinen alanlar
        for acc in accounts:
            domain = (acc.get("domain") or "").lower()
            uname = acc.get("username")
            if not uname:
                continue
            if "github" in domain:
                data["github_link"] = f"https://github.com/{uname}"
            elif "twitter" in domain or "x.com" in domain:
                data["twitter_link"] = f"https://x.com/{uname}"

        return ProfileFetch(
            url=entry.get("profileUrl") or f"https://gravatar.com/{identifier}",
            data=data,
            raw=resp.content,
            http_status=resp.status_code,
        )

    def entity_attributes(self, data: dict[str, Any], identifier: str) -> dict[str, Any]:
        attrs = super().entity_attributes(data, identifier)
        attrs["type"] = "email_profile"
        if data.get("email"):
            attrs["email"] = data["email"]
        if data.get("accounts"):
            attrs["linked_accounts"] = [
                {"domain": a.get("domain"), "username": a.get("username")}
                for a in data["accounts"]
            ]
        return attrs

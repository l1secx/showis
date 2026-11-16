"""Stack Overflow connector (Tier A — StackExchange public API).

`api.stackexchange.com/2.3/users?inname=` ucu **gzip** yanit dondurur;
`httpx` `Accept-Encoding` ile otomatik cozer (ekstra kod gerekmez).

`inname` alt-dizi eslesmesi yapar (tam eslesme degil): once `display_name`
normalize edilerek birebir eslesen aday aranir, yoksa ilk sonuc kullanilir ve
`match="fuzzy"` isaretlenir — skorlama katmani bu ipucunu kullanabilir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from showis_contracts import Platform

from .base import ProfileFetch, PublicProfileConnector

_API = "https://api.stackexchange.com/2.3/users"


def _epoch_to_iso(value: Any) -> str | None:
    """StackExchange epoch saniye dondurur -> ISO 8601 (UTC)."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _norm(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


class StackOverflowConnector(PublicProfileConnector):
    name = "stackoverflow"
    platform = Platform.STACKOVERFLOW
    profile_url_template = "https://stackoverflow.com/users/{id}"
    identifier_pattern = r"stackoverflow\.com/users/(?:\d+/)?([A-Za-z0-9_.-]+)"

    async def fetch(self, identifier: str) -> ProfileFetch | None:
        # filter=default: display_name, reputation, location, link, profile_image,
        # creation_date, badge_counts alanlarini dondurur.
        params = f"?inname={identifier}&site=stackoverflow&filter=default&pagesize=10"
        resp = await self.http_client.get(
            f"{_API}{params}",
            headers={"Accept": "application/json"},
            check_robots=False,
            follow_redirects=True,
        )
        if resp.status_code in (400, 403, 404):
            # 400: gecersiz parametre, 403: erisim kapali, 404: bilinmeyen site/yol
            return None
        resp.raise_for_status()

        body: dict[str, Any] = resp.json()
        if body.get("error_id"):
            # orn. throttling_violation / bad_parameter
            return None
        items = body.get("items") or []
        if not isinstance(items, list) or not items:
            return None

        wanted = _norm(identifier)
        user = next(
            (item for item in items if _norm(str(item.get("display_name") or "")) == wanted),
            items[0],
        )
        is_exact = _norm(str(user.get("display_name") or "")) == wanted

        link = user.get("link")
        return ProfileFetch(
            url=link or self.profile_url(identifier),
            data={
                "username": identifier,
                "name": user.get("display_name"),
                "bio": None,
                "location": user.get("location"),
                "avatar_url": user.get("profile_image"),
                "created_at": _epoch_to_iso(user.get("creation_date")),
                "reputation": user.get("reputation"),
                "badge_counts": user.get("badge_counts") or {},
                "user_id": user.get("user_id"),
                "account_id": user.get("account_id"),
                "profile_url": link,
                "match": "exact" if is_exact else "fuzzy",
            },
            raw=resp.content,
            http_status=resp.status_code,
        )

    def entity_attributes(self, data: dict[str, Any], identifier: str) -> dict[str, Any]:
        attrs = super().entity_attributes(data, identifier)
        for key in ("reputation", "badge_counts", "user_id", "account_id", "match"):
            if data.get(key) is not None:
                attrs[key] = data[key]
        return attrs

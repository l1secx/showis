"""Deterministik pivot motoru (Maltego "transform" muadili).

Bir havuz öğesinden türetilebilen yeni öğe ve ilişkileri üretir. Çıktılar
`proposed` olarak havuza yazılır; ilişkiler `source="pivot"` ile `proposed` kalır.
Yalnız herkese açık kaynaklar: ağ gerektiren pivotlar (`network=True`) mevcut
SSRF/robots politikalı bileşenleri kullanır, giriş duvarı/CAPTCHA aşılmaz.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from showis.db.models import Case, PoolItem
from showis.services.pool import create_item
from showis.services.reasoning.graph import upsert_relation
from showis_engine.variants import generate_variants
from sqlalchemy.ext.asyncio import AsyncSession

FREE_MAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "proton.me",
        "protonmail.com",
        "yandex.com",
        "yandex.com.tr",
        "mynet.com",
        "gmx.com",
    }
)

#: Kullanıcı adından aday profil adresleri (doğrulanmamış; yalnız öneri).
PROFILE_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("github", "https://github.com/{u}"),
    ("gitlab", "https://gitlab.com/{u}"),
    ("x", "https://x.com/{u}"),
    ("instagram", "https://www.instagram.com/{u}/"),
    ("reddit", "https://www.reddit.com/user/{u}"),
    ("medium", "https://medium.com/@{u}"),
    ("keybase", "https://keybase.io/{u}"),
    ("dev.to", "https://dev.to/{u}"),
)
_HANDLE_OK = re.compile(r"^[a-z0-9][a-z0-9._-]{2,38}$")
MAX_PIVOT_ITEMS = 12


@dataclass(frozen=True)
class Pivot:
    id: str
    label: str
    kinds: tuple[str, ...]
    description: str
    network: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kinds": list(self.kinds),
            "description": self.description,
            "network": self.network,
        }


PIVOTS: tuple[Pivot, ...] = (
    Pivot(
        "email_username",
        "Kullanıcı adı türet",
        ("email",),
        "E-posta yerel kısmını kullanıcı adı adayı yapar.",
    ),
    Pivot("email_domain", "Alan adı çıkar", ("email",), "Kurumsal e-posta alan adını havuza alır."),
    Pivot(
        "email_gravatar",
        "Gravatar profili",
        ("email",),
        "E-posta özetinden herkese açık Gravatar adresi.",
    ),
    Pivot(
        "email_breach",
        "Sızıntı kontrolü",
        ("email",),
        "Anahtarsız sızıntı sağlayıcı zinciri.",
        True,
    ),
    Pivot(
        "username_profiles",
        "Aday profiller",
        ("username",),
        "Yaygın platformlarda aday profil adresleri (doğrulanmamış).",
    ),
    Pivot(
        "name_usernames",
        "Kullanıcı adı permütasyonları",
        ("name", "person"),
        "Addan olası kullanıcı adı varyantları.",
    ),
    Pivot("domain_site", "Site adresi", ("domain",), "Alan adının ana sayfa adresi."),
    Pivot(
        "url_fetch",
        "Sayfayı oku",
        ("url",),
        "Sayfadaki profil/e-posta bağlantılarını çıkarır ve kanıt özeti saklar.",
        True,
    ),
)
_PIVOT_BY_ID = {pivot.id: pivot for pivot in PIVOTS}


class PivotError(ValueError):
    pass


def pivots_for(kind: str) -> list[Pivot]:
    return [pivot for pivot in PIVOTS if kind in pivot.kinds]


@dataclass
class PivotResult:
    pivot: str
    items: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pivot": self.pivot,
            "items": self.items,
            "relations": self.relations,
            "notes": self.notes,
        }


class _Ctx:
    def __init__(self, db: AsyncSession, case: Case, item: PoolItem, actor: str):
        self.db, self.case, self.item, self.actor = db, case, item, actor
        self.result = PivotResult(pivot="")

    async def add_item(
        self,
        kind: str,
        value: str,
        label: str,
        *,
        confidence: float,
        attributes: dict[str, Any] | None = None,
        reason: str = "",
    ) -> PoolItem | None:
        if len(self.result.items) >= MAX_PIVOT_ITEMS:
            return None
        created, is_new = await create_item(
            self.db,
            case_id=self.case.id,
            kind=kind,
            label=label,
            value=value,
            source="scan",
            status="proposed",
            confidence=confidence,
            attributes=attributes,
            provenance={"pivot": self.result.pivot, "from_item": self.item.id, "reason": reason},
            created_by=self.actor,
        )
        self.result.items.append(
            {
                "id": created.id,
                "kind": created.kind,
                "value": created.value[:300],
                "status": created.status,
                "created": is_new,
            }
        )
        return created

    async def relate(
        self, src: PoolItem, dst: PoolItem, rel_type: str, weight: float, note: str
    ) -> None:
        row, _ = await upsert_relation(
            self.db,
            case_id=self.case.id,
            src_item_id=src.id,
            dst_item_id=dst.id,
            type=rel_type,
            confidence=min(0.9, weight / 4),
            evidence=[
                {
                    "rule": f"pivot:{self.result.pivot}",
                    "source": self.item.id,
                    "weight": weight,
                    "note": note,
                }
            ],
            status="proposed",
            source="pivot",
            created_by=self.actor,
        )
        self.result.relations.append({"id": row.id, "type": rel_type})


def _email_parts(item: PoolItem) -> tuple[str, str]:
    local, _, domain = item.value.strip().lower().partition("@")
    return local.split("+", 1)[0], domain


async def _email_username(ctx: _Ctx) -> None:
    local, _ = _email_parts(ctx.item)
    handle = local.replace(".", "") if "." in local and len(local) > 18 else local
    if not _HANDLE_OK.match(handle):
        ctx.result.notes.append("Yerel kısım kullanıcı adı adayı için uygun değil.")
        return
    user = await ctx.add_item(
        "username",
        handle,
        f"Kullanıcı adı adayı (@{handle})",
        confidence=0.3,
        attributes={"derived_from": "email_local"},
        reason="e-posta yerel kısmı",
    )
    if user is not None:
        await ctx.relate(user, ctx.item, "uses_email", 1.0, "yerel kısım eşleşmesi")


async def _email_domain(ctx: _Ctx) -> None:
    _, domain = _email_parts(ctx.item)
    if not domain or domain in FREE_MAIL_DOMAINS:
        ctx.result.notes.append("Genel e-posta sağlayıcısı; alan adı pivotu anlamsız.")
        return
    node = await ctx.add_item(
        "domain", domain, f"Alan adı ({domain})", confidence=0.5, reason="e-posta alan adı"
    )
    if node is not None:
        await ctx.relate(ctx.item, node, "linked_from", 1.0, "e-posta alan adı")


async def _email_gravatar(ctx: _Ctx) -> None:
    digest = hashlib.md5(ctx.item.value.strip().lower().encode("utf-8")).hexdigest()  # noqa: S324 - Gravatar protokolü
    url = f"https://gravatar.com/{digest}"
    node = await ctx.add_item(
        "url",
        url,
        "Gravatar profili (aday)",
        confidence=0.2,
        attributes={"platform": "gravatar", "profile_url": url, "unverified": True},
        reason="Gravatar e-posta özeti",
    )
    if node is not None:
        await ctx.relate(node, ctx.item, "uses_email", 1.0, "Gravatar özeti e-postadan türetildi")


async def _email_breach(ctx: _Ctx) -> None:
    from showis.services.breach import BreachError, check_email

    try:
        report = await check_email(ctx.db, ctx.item.value)
    except BreachError as exc:
        ctx.result.notes.append(str(exc)[:200])
        return
    attributes = dict(ctx.item.attributes or {})
    attributes["breach_check"] = report
    ctx.item.attributes = attributes
    ctx.result.notes.append(
        f"Sızıntı: {report.get('count', 0)} ihlal"
        + (", parola sızmış" if report.get("password_exposed") else "")
    )


async def _username_profiles(ctx: _Ctx) -> None:
    handle = ctx.item.value.strip().lstrip("@").lower()
    if not _HANDLE_OK.match(handle):
        ctx.result.notes.append("Kullanıcı adı profil adresi üretmek için uygun değil.")
        return
    for platform, template in PROFILE_TEMPLATES:
        url = template.format(u=quote(handle, safe="._-"))
        node = await ctx.add_item(
            "url",
            url,
            f"{platform} — {handle} (aday)",
            confidence=0.2,
            attributes={
                "platform": platform,
                "username": handle,
                "profile_url": url,
                "unverified": True,
            },
            reason="aday profil adresi",
        )
        if node is not None:
            await ctx.relate(ctx.item, node, "owns_account", 0.5, "aday profil; doğrulanmadı")
    ctx.result.notes.append("Adresler doğrulanmadı; açık kaynakta teyit edilmeli.")


async def _name_usernames(ctx: _Ctx) -> None:
    parts = ctx.item.value.split()
    if len(parts) < 2:
        ctx.result.notes.append("Ad-soyad gerekli (en az iki sözcük).")
        return
    variants = generate_variants(parts[0], " ".join(parts[1:]), max_variants=20)
    for variant in variants.variants[:8]:
        user = await ctx.add_item(
            "username",
            variant.username,
            f"Kullanıcı adı varyantı ({variant.pattern})",
            confidence=round(variant.confidence * 0.5, 2),
            attributes={"derived_from": "name", "pattern": variant.pattern},
            reason="ad-soyad permütasyonu",
        )
        if user is not None:
            await ctx.relate(ctx.item, user, "owns_account", 0.3, "ad permütasyonu (zayıf)")


async def _domain_site(ctx: _Ctx) -> None:
    domain = ctx.item.value.strip().lower().rstrip("/.")
    url = f"https://{domain}"
    node = await ctx.add_item(
        "url",
        url,
        f"Site ({domain})",
        confidence=0.4,
        attributes={"platform": "web", "profile_url": url},
        reason="alan adı ana sayfası",
    )
    if node is not None:
        await ctx.relate(node, ctx.item, "linked_from", 1.0, "alan adı ana sayfası")


async def _url_fetch(ctx: _Ctx) -> None:
    from showis.services.research import ToolError, _fetch_public_page

    # Kullanıcının açıkça havuza koyduğu URL'de robots atlanabilir (ürün istisnası).
    respect_robots = ctx.item.source != "user"
    try:
        page = await _fetch_public_page(ctx.item.value, respect_robots=respect_robots)
    except ToolError as exc:
        ctx.result.notes.append(str(exc)[:200])
        return
    attributes = dict(ctx.item.attributes or {})
    attributes["snapshot"] = {
        "sha256": page.digest,
        "title": page.title[:200],
        "text": page.text[:4000],
    }
    ctx.item.attributes = attributes
    for profile in page.profiles[:8]:
        user = await ctx.add_item(
            "username",
            profile.identifier,
            f"{profile.platform.value} — {profile.identifier}",
            confidence=0.5,
            attributes={"platform": profile.platform.value, "profile_url": profile.url},
            reason="sayfadaki profil bağlantısı",
        )
        link = await ctx.add_item(
            "url",
            profile.url,
            f"{profile.platform.value} profili",
            confidence=0.4,
            attributes={
                "platform": profile.platform.value,
                "username": profile.identifier,
                "profile_url": profile.url,
                "source_url": ctx.item.value,
            },
            reason="sayfadaki profil bağlantısı",
        )
        if user is not None:
            await ctx.relate(ctx.item, user, "linked_from", 1.5, "sayfa profili listeliyor")
        if link is not None:
            await ctx.relate(ctx.item, link, "linked_from", 1.5, "sayfa profili listeliyor")
    for email in page.emails[:4]:
        node = await ctx.add_item(
            "email",
            email,
            f"E-posta ({email})",
            confidence=0.5,
            attributes={"source_url": ctx.item.value},
            reason="sayfada geçen e-posta",
        )
        if node is not None:
            await ctx.relate(ctx.item, node, "linked_from", 1.0, "sayfada geçiyor")
    ctx.result.notes.append(f"Sayfa özeti saklandı (sha256 {page.digest[:12]}…).")


_HANDLERS: dict[str, Callable[[_Ctx], Awaitable[None]]] = {
    "email_username": _email_username,
    "email_domain": _email_domain,
    "email_gravatar": _email_gravatar,
    "email_breach": _email_breach,
    "username_profiles": _username_profiles,
    "name_usernames": _name_usernames,
    "domain_site": _domain_site,
    "url_fetch": _url_fetch,
}


async def run_pivot(
    db: AsyncSession, *, case: Case, item: PoolItem, pivot_id: str, actor: str = "pivot"
) -> PivotResult:
    pivot = _PIVOT_BY_ID.get(pivot_id)
    if pivot is None:
        raise PivotError(f"bilinmeyen pivot: {pivot_id}")
    if item.kind not in pivot.kinds:
        raise PivotError(f"{pivot_id} pivotu {item.kind} öğesine uygulanamaz")
    if item.status == "rejected":
        raise PivotError("reddedilmiş öğe üzerinde pivot çalıştırılamaz")
    ctx = _Ctx(db, case, item, actor)
    ctx.result.pivot = pivot.id
    await _HANDLERS[pivot.id](ctx)
    await db.commit()
    return ctx.result

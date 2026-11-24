"""Derin araştırma için deterministik oyun kitabı.

Bu modül LLM'siz de çalışan katmandır: kimlik tohumlarını birleştirir, arama
sorguları üretir, sayfa içeriğinden profil bağlantılarını ve iletişim
bilgilerini çıkarır, biyografi için yedek metin üretir. Ağ veya DB erişimi
yoktur; böylece ajan dalgaları ve birim testleri aynı davranışı paylaşır.

Güvenlik: üretilen sorgular `sanitize_hint` benzeri nötralizasyondan geçer;
sayfa içeriği **veri** kabul edilir, talimat olarak yorumlanmaz.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from showis_connectors.websearch.normalize import extract_platform_from_url
from showis_contracts import Platform

#: Kişisel bağlantı toplayıcıları. Bu sayfalar profil değil, profil listesidir;
#: getirilip içindeki bağlantılar izlenmelidir.
AGGREGATOR_HOSTS: frozenset[str] = frozenset(
    {
        "linktr.ee",
        "linktree.com",
        "bio.link",
        "beacons.ai",
        "about.me",
        "carrd.co",
        "solo.to",
        "tap.bio",
        "linkr.bio",
        "allmylinks.com",
        "campsite.bio",
        "taplink.cc",
        "lnk.bio",
        "direct.me",
        "guns.lol",
        "bio.site",
        "flow.page",
        "stan.store",
        "koji.to",
        "withkoji.com",
        "milkshake.app",
        "shor.by",
        "snipfeed.co",
    }
)

#: Toplayıcı sayfaların gövdesinde değil, alt bilgisinde geçen marka
#: kullanıcı adları. Bunları bulgu saymak yanlış pozitif üretir.
_BRAND_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "linktree",
        "linktr",
        "beacons",
        "aboutme",
        "carrd",
        "instagram",
        "facebook",
        "twitter",
        "linkedin",
        "youtube",
        "tiktok",
        "pinterest",
        "snapchat",
        "threads",
        "discord",
        "telegram",
        "spotify",
        "apple",
        "google",
        "github",
        "features",
        "blog",
        "blogs",
        "tools",
        "link-in-bio",
        "consent",
        "cep",
        "d",
        "s",
        "solutions",
        "og",
        "sticker",
        "profiles",
        "fonts",
        "graphql",
        "assets",
        "ugc",
        "ingress",
        "digital-business-cards",
        "consent-scripts",
        "i",
        "pricing",
        "marketplace",
        "topics",
        "explore",
        "orgs",
        "sponsors",
        "collections",
        "trending",
        "notifications",
        "security",
        "enterprise",
        "careers",
        "press",
        "contact",
        "privacy",
        "terms",
        "support",
        "help",
        "about",
        "home",
        "login",
        "signup",
        "search",
        "settings",
        "share",
        "download",
        "threads.com",
        "x.com",
        "showis-osint",
        "showis",
        "join",
        "customer-stories",
        "codeql",
        "docs",
        "_u",
        "web",
        "popular",
        "legal",
        "accounts",
        "developer",
        "reel",
        "reels",
        "stories",
        "direct",
        "new",
        "readme",
        "site",
    }
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\w.])(?:\+?\d[\d\s().-]{8,17}\d)(?![\w.])")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_HANDLE_RE = re.compile(r"[^a-z0-9._-]+")
_MAX_HANDLES = 8


_ASSET_EXTENSIONS: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".txt",
    ".woff",
    ".woff2",
    ".mp4",
    ".webm",
)


def _looks_like_asset(identifier: str) -> bool:
    return identifier.casefold().endswith(_ASSET_EXTENSIONS)


def clean_handle(value: object) -> str:
    """Kullanıcı adını sorgu/URL için güvenli biçime getirir."""
    text = str(value or "").strip().lstrip("@").strip().lower()
    if not text:
        return ""
    if _CONTROL_RE.search(text):
        return ""
    text = _HANDLE_RE.sub("", text)
    return text.strip("._-")[:64]


def _clean_text(value: object, *, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    if _CONTROL_RE.search(text):
        text = _CONTROL_RE.sub(" ", text)
    return text[:limit].strip()


def _quote(term: object, *, limit: int = 120) -> str:
    """Terimi çift tırnak içine alır; tırnak/operatör karakterlerini temizler."""
    text = _clean_text(term, limit=limit)
    text = text.replace('"', " ").replace("'", " ").replace("\\", " ")
    text = re.sub(r"\b(site|filetype|inurl|intitle|intext)\s*:", r"\1_", text, flags=re.I)
    text = re.sub(r"\b(OR|AND|NOT|NEAR)\b", lambda m: m.group(1).lower(), text)
    return " ".join(text.split())


@dataclass(frozen=True)
class IdentityProfile:
    """Vaka hedefi, seed'ler ve havuzdan birleştirilmiş kimlik tohumları."""

    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    handles: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    organizations: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()

    @property
    def primary_handle(self) -> str:
        return self.handles[0] if self.handles else ""

    @property
    def has_name(self) -> bool:
        return bool(self.full_name)

    def handle_terms(self, *, limit: int = 5) -> tuple[str, ...]:
        return tuple(dict.fromkeys(h for h in (*self.handles, *self.variants) if h))[:limit]


def build_identity_profile(
    *,
    first_name: object = "",
    last_name: object = "",
    nickname: object = "",
    email: object = "",
    phone: object = "",
    domain: object = "",
    seed_usernames: Iterable[object] = (),
    seed_urls: Iterable[object] = (),
    seed_emails: Iterable[object] = (),
    pool_items: Iterable[dict[str, Any]] = (),
) -> IdentityProfile:
    """Tüm kimlik kaynaklarını tek profilde birleştirir.

    Kullanıcı seed'i (nickname + seed kullanıcı adları) isimden önce gelir:
    seed hesabı kanıttır, isimden türetilen varyant ise tahmindir.
    """
    handles: list[str] = []
    names: list[str] = []
    emails: list[str] = []
    phones: list[str] = []
    domains: list[str] = []
    locations: list[str] = []
    organizations: list[str] = []
    urls: list[str] = []

    first = _clean_text(first_name, limit=80)
    last = _clean_text(last_name, limit=80)

    def add_handle(value: object) -> None:
        handle = clean_handle(value)
        if handle and handle not in handles:
            handles.append(handle)

    def add_unique(target: list[str], value: object, *, limit: int = 120) -> None:
        text = _clean_text(value, limit=limit)
        if not text:
            return
        if text.casefold() not in {item.casefold() for item in target}:
            target.append(text)

    add_handle(nickname)
    for username in seed_usernames:
        add_handle(username)
    for email in seed_emails:
        add_unique(emails, str(email or "").casefold(), limit=254)
    for url in seed_urls:
        text = str(url or "").strip()
        if not text.startswith(("http://", "https://")):
            continue
        add_unique(urls, text, limit=2048)
        platform, subject = extract_platform_from_url(text)
        if platform not in {Platform.WEB, Platform.UNKNOWN} and subject:
            add_handle(subject)
        else:
            social = _social_identifier(text)
            if social is not None:
                add_handle(social[1])
    if first and last:
        add_unique(names, f"{first} {last}", limit=120)
    add_unique(emails, str(email or "").casefold(), limit=254)
    add_unique(phones, str(phone or ""), limit=32)
    add_unique(domains, str(domain or "").lower().strip("."), limit=253)

    for item in pool_items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        value = item.get("value")
        raw_attrs = item.get("attributes")
        attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
        if kind == "username" or kind == "identifier":
            add_handle(value)
        elif kind == "name":
            add_unique(names, value)
        elif kind == "email":
            add_unique(emails, str(value or "").casefold(), limit=254)
        elif kind == "phone":
            add_unique(phones, value, limit=32)
        elif kind == "domain":
            add_unique(domains, str(value or "").lower().strip("."), limit=253)
        elif kind == "location":
            add_unique(locations, value)
        elif kind == "organization":
            add_unique(organizations, value)
        elif kind in {"url", "person"}:
            text = str(value or "").strip()
            if text.startswith(("http://", "https://")):
                add_unique(urls, text, limit=2048)
                platform, subject = extract_platform_from_url(text)
                if platform not in {Platform.WEB, Platform.UNKNOWN} and subject:
                    add_handle(subject)
        elif kind == "event":
            add_unique(locations, attrs.get("place"))
            add_unique(organizations, attrs.get("organization"))

    full_name = names[0] if names else ""
    variants: list[str] = []
    if first or last:
        try:
            from showis_engine.variants import generate_variants

            generated = generate_variants(
                first, last, {}, nickname=handles[0] if handles else "", max_variants=24
            )
            for variant in generated.variants:
                handle = clean_handle(variant.username)
                if handle and handle not in handles and handle not in variants:
                    variants.append(handle)
        except Exception:  # noqa: BLE001 - varyant üretimi profili düşürmesin
            pass

    return IdentityProfile(
        full_name=full_name,
        first_name=first,
        last_name=last,
        handles=tuple(handles[:_MAX_HANDLES]),
        variants=tuple(variants[:12]),
        emails=tuple(emails[:12]),
        phones=tuple(phones[:8]),
        domains=tuple(domains[:8]),
        locations=tuple(locations[:8]),
        organizations=tuple(organizations[:8]),
        urls=tuple(urls[:24]),
    )


@dataclass(frozen=True)
class ResearchQuery:
    text: str
    reason: str
    priority: int = 3


def _q(text: str, reason: str, priority: int = 3) -> ResearchQuery:
    cleaned = _clean_text(text, limit=200)
    return ResearchQuery(text=cleaned, reason=reason, priority=priority)


def build_baseline_queries(profile: IdentityProfile) -> list[ResearchQuery]:
    """LLM'den bağımsız, mutlaka çalışan kimlik kapsama sorguları."""
    queries: list[ResearchQuery] = []
    full = profile.full_name
    handles = profile.handle_terms(limit=4)
    primary = profile.primary_handle

    if full and primary:
        queries.append(
            _q(
                f'"{_quote(full)}" "{_quote(primary)}"',
                "Ad ve seed kullanıcı adını birlikte doğrulama",
                1,
            )
        )
        queries.append(
            _q(
                f'"{_quote(primary)}" "{_quote(full)}"',
                "Seed hesabı ile tam isim eşleştirme",
                1,
            )
        )
    if full:
        queries.append(_q(f'"{_quote(full)}"', "Tam isim web araması", 1))
        queries.append(
            _q(f'"{_quote(full)}" site:linkedin.com/in', "LinkedIn herkese açık profil", 2)
        )
        queries.append(_q(f'"{_quote(full)}" email OR contact', "İletişim bilgisi izi", 3))
        queries.append(_q(f'"{_quote(full)}" site:linktr.ee', "Bağlantı sayfası", 2))
        for org in profile.organizations[:2]:
            queries.append(
                _q(
                    f'"{_quote(full)}" "{_quote(org)}"',
                    "İsim ve kurum birlikte doğrulama",
                    2,
                )
            )
        for location in profile.locations[:2]:
            queries.append(
                _q(
                    f'"{_quote(full)}" "{_quote(location)}"',
                    "İsim ve yer birlikte doğrulama",
                    3,
                )
            )

    name_tokens = [token for token in (profile.first_name, profile.last_name) if len(token) > 2]
    for handle in handles:
        h = _quote(handle)
        queries.append(_q(f'"{h}"', "Seed kullanıcı adı genel araması", 1))
        queries.append(_q(f'"{h}" linktree', "Kullanıcı adı bağlantı sayfası", 1))
        queries.append(_q(f'"{h}" site:linktr.ee', "Linktree profil araması", 1))
        queries.append(
            _q(
                f'"{h}" (linktree OR bio OR social OR about)',
                "Kullanıcı adı biyografi ve sosyal iz",
                2,
            )
        )
        for token in name_tokens[:2]:
            queries.append(
                _q(f'"{h}" "{_quote(token)}"', "Kullanıcı adı ve isim eşleştirme", 2)
            )
        for site, reason in (
            ("site:github.com", "GitHub profil araması"),
            ("site:instagram.com", "Instagram profil araması"),
            ("site:twitter.com OR site:x.com", "X/Twitter profil araması"),
            ("site:linkedin.com", "LinkedIn profil araması"),
            ("site:facebook.com", "Facebook profil araması"),
            ("site:tiktok.com", "TikTok profil araması"),
            ("site:youtube.com", "YouTube kanal araması"),
            ("site:reddit.com", "Reddit profil araması"),
            ("site:medium.com", "Medium profil araması"),
            ("site:about.me", "about.me profil araması"),
            ("site:beacons.ai", "Beacons profil araması"),
            ("site:carrd.co", "Carrd profil araması"),
        ):
            queries.append(_q(f'"{h}" {site}', reason, 2))
        queries.append(_q(f'"{h}" email', "Kullanıcı adı e-posta izi", 3))

    for email in profile.emails[:3]:
        queries.append(_q(f'"{_quote(email)}"', "E-posta web araması", 1))
    for phone in profile.phones[:2]:
        queries.append(_q(f'"{_quote(phone)}"', "Telefon web araması", 2))
    for domain in profile.domains[:2]:
        queries.append(_q(f'"{_quote(domain)}"', "Domain web araması", 2))

    seen: set[str] = set()
    unique: list[ResearchQuery] = []
    for query in queries:
        if len(query.text) < 3:
            continue
        key = query.text.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(query)
    unique.sort(key=lambda item: item.priority)
    return unique


def build_followup_queries(
    profile: IdentityProfile,
    *,
    known_queries: Iterable[str],
    discovered_handles: Iterable[str] = (),
    extra_terms: Iterable[str] = (),
) -> list[ResearchQuery]:
    """İlk dalgadan sonra kalan kombinasyonları üretir."""
    known = {str(q).casefold() for q in known_queries}
    handles = list(
        dict.fromkeys(
            [*profile.handle_terms(limit=6), *map(clean_handle, discovered_handles)]
        )
    )
    queries: list[ResearchQuery] = []
    full = profile.full_name
    for handle in handles[:8]:
        h = _quote(handle)
        candidates = [
            _q(f'"{h}"', "Keşfedilen kullanıcı adı araması", 2),
            _q(f'"{h}" linktree', "Bağlantı sayfası araması", 1),
            _q(f'"{h}" bio', "Biyografi araması", 2),
            _q(f'"{h}" "github" OR "gitlab"', "Kod profili araması", 3),
            _q(f'"{h}" "instagram" OR "twitter"', "Sosyal profil araması", 3),
        ]
        if full:
            candidates.append(
                _q(f'"{h}" "{_quote(full)}"', "Keşfedilen hesap ve isim eşleştirme", 1)
            )
        queries.extend(c for c in candidates if c is not None)
    for term in extra_terms:
        cleaned = _quote(term, limit=160)
        if cleaned:
            queries.append(_q(f'"{cleaned}"', "Alt ajan ipucu doğrulaması", 2))
    unique: list[ResearchQuery] = []
    seen: set[str] = set()
    for query in queries:
        if len(query.text) < 3:
            continue
        key = query.text.casefold()
        if key in known or key in seen:
            continue
        seen.add(key)
        unique.append(query)
    unique.sort(key=lambda item: item.priority)
    return unique


@dataclass(frozen=True)
class LinkRef:
    url: str
    text: str = ""


@dataclass(frozen=True)
class ProfileRef:
    platform: Platform
    identifier: str
    url: str
    text: str = ""
    aggregator: bool = False


@dataclass
class PageIntel:
    url: str
    title: str = ""
    description: str = ""
    text: str = ""
    links: list[LinkRef] = field(default_factory=list)
    profiles: list[ProfileRef] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)


class _IntelParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "canvas"}
    BLOCK_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "li",
        "tr",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "footer",
        "table",
        "ul",
        "ol",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.description = ""
        self.anchors: list[LinkRef] = []
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {key.lower(): value for key, value in attrs}
        if tag in self.SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            content = attr.get("content") or ""
            if name in {"description", "og:description", "twitter:description"} and content:
                if not self.description:
                    self.description = content[:500]
        if tag == "a" and attr.get("href"):
            self._href = attr["href"]
            self._anchor_text = []
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href is not None:
            self.anchors.append(
                LinkRef(url=self._href, text=" ".join("".join(self._anchor_text).split())[:200])
            )
            self._href = None
            self._anchor_text = []
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = html_lib.unescape(data)
        if self._in_title:
            self.title_parts.append(text)
        else:
            self.parts.append(text)
        if self._href is not None:
            self._anchor_text.append(text)

    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())[:300]

    @property
    def text(self) -> str:
        lines = [" ".join(part.split()) for part in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().split(":")[0].removeprefix("www.")
    except ValueError:
        return ""


def aggregator_domain(url: str) -> str:
    """URL'nin ait olduğu toplayıcı ailesini döndürür (yoksa "")."""
    host = host_of(url)
    if host.endswith(".carrd.co"):
        return "carrd.co"
    for item in AGGREGATOR_HOSTS:
        if host == item:
            return item
    return ""


def is_aggregator_url(url: str) -> bool:
    # linktr.ee/assets.production gibi altyapı alt alanları profil değildir.
    return bool(aggregator_domain(url))


_SOCIAL_HOSTS: frozenset[str] = frozenset(
    {
        "x.com",
        "twitter.com",
        "facebook.com",
        "linkedin.com",
        "threads.net",
        "threads.com",
        "snapchat.com",
        "discord.gg",
    }
)

_SOCIAL_RESERVED: frozenset[str] = frozenset(
    {
        "intent",
        "share",
        "home",
        "search",
        "i",
        "hashtag",
        "explore",
        "login",
        "signup",
        "help",
        "about",
        "privacy",
        "terms",
        "policies",
        "settings",
        "notifications",
        "messages",
        "watch",
        "groups",
        "events",
        "marketplace",
        "pages",
        "profile.php",
    }
)


def _social_identifier(url: str) -> tuple[Platform, str] | None:
    """X/Facebook/LinkedIn gibi enum'u olmayan sosyal ağlardan kimlik çıkarır."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().split(":")[0].removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if host in {"x.com", "twitter.com"}:
        if len(parts) != 1:
            return None
        ident = parts[0]
    elif host == "facebook.com":
        ident = parts[1] if parts[0] == "profile.php" and len(parts) > 1 else parts[0]
    elif host == "linkedin.com":
        if len(parts) != 2 or parts[0] not in {"in", "company", "school"}:
            return None
        ident = parts[1]
    elif host in {"threads.net", "threads.com"}:
        if len(parts) != 1:
            return None
        ident = parts[0].lstrip("@")
    elif host == "snapchat.com":
        ident = parts[1] if parts[0] == "add" and len(parts) > 1 else parts[0]
    elif host == "discord.gg":
        ident = parts[0]
    else:
        return None
    clean = clean_handle(ident)
    if not clean or clean in _SOCIAL_RESERVED:
        return None
    return Platform.WEB, clean


def canonical_profile(url: str) -> ProfileRef | None:
    """URL'den platform + kullanıcı adı çıkarır; marka/çöp kimlikleri eler."""
    if any(token in url for token in ("%20", "\u2192", "&quot;", '"', " ")):
        return None
    platform, identifier = extract_platform_from_url(url)
    if platform in {Platform.WEB, Platform.UNKNOWN} or not identifier:
        if is_aggregator_url(url):
            host = host_of(url)
            if host.endswith(".carrd.co"):
                ident = clean_handle(host.split(".")[0])
            else:
                parts = [p for p in urlparse(url).path.split("/") if p]
                # Kök toplayıcıda yalnız tek segmentli yol profildir:
                # /l1sec evet; /blog/x, /solutions/y, /i/z hayır.
                if len(parts) != 1:
                    return None
                ident = clean_handle(parts[0])
            if not ident or ident in _BRAND_IDENTIFIERS:
                return None
            return ProfileRef(
                platform=Platform.WEB,
                identifier=ident,
                url=url,
                aggregator=True,
            )
        social = _social_identifier(url)
        if social is not None:
            social_platform, social_ident = social
            if social_ident.casefold() in _BRAND_IDENTIFIERS:
                return None
            return ProfileRef(
                platform=social_platform,
                identifier=social_ident,
                url=url,
                aggregator=False,
            )
        return None
    host = host_of(url)
    if platform == Platform.GITHUB and host != "github.com":
        return None
    if platform == Platform.GITLAB and host != "gitlab.com":
        return None
    if platform == Platform.INSTAGRAM and host != "instagram.com":
        return None
    if platform == Platform.LINKTREE and host != "linktr.ee":
        return None
    if platform == Platform.ABOUTME and host != "about.me":
        return None
    if platform == Platform.BEACONS and host != "beacons.ai":
        return None
    if platform in {
        Platform.LINKTREE,
        Platform.ABOUTME,
        Platform.BEACONS,
        Platform.GITHUB,
        Platform.GITLAB,
        Platform.BITBUCKET,
        Platform.CODEBERG,
    }:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if len(parts) != 1:
            return None
    ident = clean_handle(identifier)
    if not ident or _looks_like_asset(ident) or ident.casefold() in _BRAND_IDENTIFIERS:
        return None
    return ProfileRef(
        platform=platform,
        identifier=ident,
        url=url,
        aggregator=is_aggregator_url(url),
    )


def parse_page_intel(url: str, markup: str, *, max_text: int = 12000) -> PageIntel:
    """HTML'den metin, başlık, bağlantılar ve iletişim bilgileri çıkarır."""
    parser = _IntelParser()
    try:
        parser.feed(markup[:900_000])
    except Exception:  # noqa: BLE001 - bozuk HTML araştırmayı düşürmesin
        pass

    intel = PageIntel(
        url=url,
        title=parser.title,
        description=parser.description,
        text=parser.text[:max_text],
    )

    seen_urls: set[str] = set()
    for anchor in parser.anchors[:400]:
        absolute = urljoin(url, anchor.url.strip())
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        if is_aggregator_url(absolute):
            # Toplayıcının kendi iç bağlantıları (privacy, login) profil değildir.
            path = urlparse(absolute).path.strip("/")
            if not path or path.split("/")[0].casefold() in _BRAND_IDENTIFIERS:
                continue
        if absolute in seen_urls:
            continue
        seen_urls.add(absolute)
        intel.links.append(LinkRef(url=absolute, text=anchor.text))
        profile = canonical_profile(absolute)
        if profile is not None:
            intel.profiles.append(
                ProfileRef(
                    platform=profile.platform,
                    identifier=profile.identifier,
                    url=absolute,
                    text=anchor.text,
                    aggregator=profile.aggregator,
                )
            )

    # Ham metinde/JSON gömülü URL olarak geçen profiller (linktree, bio metni).
    for raw_url in re.findall(r'https?://[^\s"\'<>]+', markup):
        raw_url = html_lib.unescape(raw_url)
        raw_url = re.split(r'["\'<>\s\u2192\u2190]', raw_url)[0].rstrip(".,);]")
        if raw_url in seen_urls:
            continue
        profile = canonical_profile(raw_url)
        if profile is None:
            continue
        if any(
            item.platform == profile.platform and item.identifier == profile.identifier
            for item in intel.profiles
        ):
            continue
        intel.profiles.append(profile)

    # Bir toplayıcı sayfasının kendi "keşfet/şablon" önerileri aynı ailenin
    # başka profilleridir; hedefin hesapları değildir. Aynı ailedeki
    # profilleri ele, dış platformları koru.
    page_aggregator = aggregator_domain(url)
    if page_aggregator:
        intel.profiles = [
            profile
            for profile in intel.profiles
            if aggregator_domain(profile.url) != page_aggregator
        ]

    emails: list[str] = []
    # Toplayıcı sayfalarındaki öneri kartları başka kişilerin e-postalarını
    # taşır; yalnızca doğrudan mailto bağlantısı güvenilir kabul edilir.
    text_for_email = "" if page_aggregator else parser.text
    for match in _EMAIL_RE.findall(text_for_email):
        email = match.casefold()
        if email not in emails and not email.endswith((".png", ".jpg", ".svg", ".webp")):
            emails.append(email)
    # mailto bağlantıları gövde metninde görünmeyebilir (ör. linktree e-posta
    # düğmesi); bunları ayrıca topla.
    for match in re.findall(r'mailto:([^"\'?>\s&]+)', markup, flags=re.IGNORECASE):
        email = match.casefold()
        if "@" in email and email not in emails:
            emails.append(email)
    intel.emails = emails[:20]

    phones: list[str] = []
    for match in _PHONE_RE.findall(parser.text):
        digits = re.sub(r"\D", "", match)
        if 9 <= len(digits) <= 15:
            phone = " ".join(match.split())
            if phone not in phones:
                phones.append(phone)
    intel.phones = phones[:10]
    return intel


def bio_sources(pool_items: Iterable[dict[str, Any]], limit: int = 40) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in pool_items:
        raw_provenance = item.get("provenance")
        provenance: dict[str, Any] = (
            raw_provenance if isinstance(raw_provenance, dict) else {}
        )
        url = str(provenance.get("url") or provenance.get("source_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        sources.append({"url": url, "label": str(item.get("label") or "")[:160]})
        if len(sources) >= limit:
            break
    return sources


def render_bio_markdown(draft: dict[str, Any]) -> str:
    """LLM biyografi taslağını okunabilir Markdown'a çevirir."""
    lines: list[str] = []
    headline = _clean_text(draft.get("headline"), limit=200)
    if headline:
        lines.append(f"# {headline}")
    summary = str(draft.get("summary") or "").strip()
    if summary:
        lines.extend(["", summary])

    sections = (
        ("Kimlikler", "identities"),
        ("Hesaplar", "accounts"),
        ("Konum", "locations"),
        ("Kurumlar", "organizations"),
        ("Zaman çizelgesi", "timeline"),
        ("İlgi alanları", "interests"),
        ("Risk notları", "risk_notes"),
    )
    for title, key in sections:
        values = draft.get(key) or []
        if not isinstance(values, list):
            values = [values]
        clean = [_clean_text(value, limit=400) for value in values if _clean_text(value, limit=400)]
        if clean:
            lines.extend(["", f"## {title}"] + [f"- {value}" for value in clean])
    return "\n".join(lines).strip()


def fallback_bio(profile: IdentityProfile, pool_items: Iterable[dict[str, Any]]) -> str:
    """LLM yoksa/başarısızsa havuzdan deterministik biyografi üretir."""
    items = list(pool_items)
    lines: list[str] = []
    name = profile.full_name or profile.primary_handle or "Hedef"
    lines.append(f"# {name}")
    summary_bits: list[str] = []
    if profile.primary_handle:
        summary_bits.append(f"Bilinen kullanıcı adı: @{profile.primary_handle}.")
    if profile.emails:
        summary_bits.append(f"Bilinen e-posta: {', '.join(profile.emails[:3])}.")
    if profile.locations:
        summary_bits.append(f"Konum izi: {', '.join(profile.locations[:3])}.")
    if profile.organizations:
        summary_bits.append(f"Kurum izi: {', '.join(profile.organizations[:3])}.")
    if summary_bits:
        lines.extend(["", " ".join(summary_bits)])

    groups: list[tuple[str, list[str]]] = [
        ("Kullanıcı adları", [str(i.get("value")) for i in items if i.get("kind") == "username"]),
        ("Hesaplar", [str(i.get("value")) for i in items if i.get("kind") == "url"]),
        ("E-postalar", [str(i.get("value")) for i in items if i.get("kind") == "email"]),
        (
            "Kurumlar ve yerler",
            [
                str(i.get("value"))
                for i in items
                if i.get("kind") in {"organization", "location"}
            ],
        ),
        ("Olaylar", [str(i.get("value")) for i in items if i.get("kind") == "event"]),
    ]
    for title, values in groups:
        unique = list(dict.fromkeys(value for value in values if value))[:20]
        if unique:
            lines.extend(["", f"## {title}"] + [f"- {value}" for value in unique])

    sources = bio_sources(items)
    if sources:
        lines.extend(["", "## Kaynaklar"] + [f"- {item['url']}" for item in sources[:15]])
    return "\n".join(lines).strip()

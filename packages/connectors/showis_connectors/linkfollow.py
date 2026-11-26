"""Herkese açık bağlantı toplayıcı sayfalarını izleyen connector.

Linktree, about.me, beacons.ai gibi sayfalar bir kişinin diğer profillerine
giden bağlantıları herkese açık biçimde listeler. Bu connector sayfayı bir kez
okur, içindeki platform profillerini Entity'ye çevirir; scanner'ın mevcut
pivot mekanizması bu profilleri ilgili connector'larda doğrular.

Güvenlik: URL ve yönlendirmeler SSRF/robots kapısından geçer; giriş duvarı,
CAPTCHA veya erişim kontrolü aşılmaz. Sayfa içeriği veri kabul edilir.
"""

from __future__ import annotations

import hashlib
import html
import re
import uuid
from urllib.parse import urljoin, urlparse

from showis_contracts import (
    Edge,
    EdgeType,
    Entity,
    EntityType,
    Observation,
    ObservationKind,
    Platform,
    RiskTier,
    ScanContext,
    TaskSpec,
    make_entity_id,
)
from showis_engine.edge_ids import short_edge_id
from showis_engine.htmlutil import meta_content, og_title, strip_html
from showis_engine.http import EvidenceStore, HttpClient
from showis_engine.policy import PolicyViolationError, guard_url

_HREF_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_RAW_URL_RE = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<![\w.])(?:\+?\d[\d\s().-]{8,17}\d)(?![\w.])")
_MAX_TEXT = 12_000
_MAX_BYTES = 900_000
_MAX_LINKS = 80

_AGGREGATOR_HOSTS = {
    "linktr.ee",
    "linktree.com",
    "about.me",
    "beacons.ai",
    "carrd.co",
    "bio.link",
    "solo.to",
    "tap.bio",
    "linkr.bio",
    "allmylinks.com",
    "campsite.bio",
    "taplink.cc",
    "lnk.bio",
    "direct.me",
    "bio.site",
    "flow.page",
    "guns.lol",
    "stan.store",
}

_BRAND_HANDLES = {
    "linktree",
    "linktr",
    "blog",
    "blogs",
    "features",
    "tools",
    "link-in-bio",
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
    "beacons",
    "aboutme",
    "carrd",
    "instagram",
    "facebook",
    "twitter",
    "linkedin",
    "youtube",
    "tiktok",
    "github",
    "privacy",
    "terms",
    "support",
    "help",
    "about",
    "home",
    "login",
    "signup",
    "share",
}


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().split(":")[0].removeprefix("www.")


def _aggregator_domain(url: str) -> str:
    host = _host(url)
    if host.endswith(".carrd.co"):
        return "carrd.co"
    for item in _AGGREGATOR_HOSTS:
        if host == item:
            return item
    return ""


def is_aggregator_url(url: str) -> bool:
    return bool(_aggregator_domain(url))


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


def _clean_identifier(value: str) -> str:
    text = re.sub(r"[^a-z0-9._-]+", "", str(value or "").strip().lower())
    return text.strip("._-")[:64]


_SOCIAL_WEB_HOSTS = {
    "x.com",
    "twitter.com",
    "facebook.com",
    "linkedin.com",
    "threads.net",
    "threads.com",
    "snapchat.com",
    "discord.gg",
}
_SOCIAL_WEB_RESERVED = {
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
    "settings",
    "watch",
    "groups",
    "events",
    "marketplace",
    "pages",
    "profile.php",
}


def _social_web_identifier(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().split(":")[0].removeprefix("www.")
    if host not in _SOCIAL_WEB_HOSTS:
        return None
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
    clean = _clean_identifier(ident)
    if not clean or clean in _SOCIAL_WEB_RESERVED or clean in _BRAND_HANDLES:
        return None
    return clean


def _profile_from_url(url: str) -> tuple[Platform, str] | None:
    """Platform + kullanıcı adı; bilinmeyen veya marka hesapları elenir."""
    from showis_connectors.websearch.normalize import extract_platform_from_url

    platform, identifier = extract_platform_from_url(url)
    host = _host(url)
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
        parts = [part for part in urlparse(url).path.split("/") if part]
        if len(parts) != 1:
            return None
    if platform == Platform.UNKNOWN or not identifier:
        social = _social_web_identifier(url)
        return (Platform.WEB, social) if social else None
    if platform == Platform.WEB:
        social = _social_web_identifier(url)
        return (Platform.WEB, social) if social else None
    ident = _clean_identifier(identifier)
    if not ident or _looks_like_asset(ident) or ident in _BRAND_HANDLES:
        return None
    return platform, ident


def _profile_url(platform: Platform, identifier: str) -> str:
    return f"https://{platform.value}/{identifier}"


class LinkFollowConnector:
    """Bağlantı toplayıcı sayfalarını okuyup profil Entity'leri üretir."""

    name = "linkfollow"
    platform = Platform.WEB
    tier = RiskTier.B
    supported_kinds = {"fetch_page"}
    detection = "linkfollow"

    def __init__(
        self,
        http_client: HttpClient | None = None,
        evidence_store: EvidenceStore | None = None,
        max_urls: int = 10,
    ):
        self.http_client = http_client or HttpClient(enable_ssrf_guard=True)
        self.evidence_store = evidence_store or EvidenceStore()
        self.max_urls = max_urls

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------
    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        # O1 hızlı kalmalı: sayfa çekme yalnızca O2/O3'te planlanır.
        # `norm_mode` alias'ları da ("FAST"/"HIZLI"/"O1-...") O1'e indirger.
        from showis_engine.scan_modes import norm_mode

        if norm_mode(ctx.mode) == "O1":
            return []
        urls: list[str] = []
        for seed in ctx.seeds:
            if seed.url and str(seed.url).startswith(("http://", "https://")):
                urls.append(str(seed.url))
        nickname = _clean_identifier(ctx.target.nickname)
        if nickname:
            urls.extend(
                [
                    f"https://linktr.ee/{nickname}",
                    f"https://about.me/{nickname}",
                    f"https://beacons.ai/{nickname}",
                    f"https://{nickname}.carrd.co",
                ]
            )
        tasks: list[TaskSpec] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            if not is_aggregator_url(url) and not _profile_from_url(url):
                continue
            tasks.append(
                TaskSpec(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=Platform.WEB,
                    kind="fetch_page",
                    params={"url": url},
                    priority=2,
                )
            )
            if len(tasks) >= self.max_urls:
                break
        return tasks

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------
    async def run(self, task: TaskSpec, ctx: ScanContext) -> list[Observation]:
        raw_url = str(task.params.get("url") or "").strip()
        if not raw_url.startswith(("http://", "https://")):
            return []
        try:
            url = guard_url(raw_url)
        except (PolicyViolationError, Exception):  # noqa: BLE001 - policy guard
            return [
                Observation(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=Platform.WEB,
                    kind=ObservationKind.BLOCKED,
                    subject=raw_url,
                    tier=self.tier,
                    data={},
                    blocked=True,
                )
            ]
        try:
            response = await self.http_client.get(url, check_robots=True)
        except Exception as exc:  # noqa: BLE001 - ağ hatası gözlem olarak döner
            return [
                Observation(
                    id=str(uuid.uuid4()),
                    connector=self.name,
                    platform=Platform.WEB,
                    kind=ObservationKind.BLOCKED,
                    subject=url,
                    tier=self.tier,
                    data={},
                    error=f"{type(exc).__name__}",
                    blocked=True,
                )
            ]
        if response.status_code != 200:
            return []
        content_type = (response.headers.get("content-type") or "").lower()
        if not any(token in content_type for token in ("text/html", "text/plain", "xhtml")):
            return []
        raw = response.content[:_MAX_BYTES]
        if not raw:
            return []
        try:
            markup = raw.decode(response.encoding or "utf-8", errors="replace")
        except LookupError:
            markup = raw.decode("utf-8", errors="replace")

        title = (og_title(markup) or meta_content(markup, name="description") or "").strip()
        if not title:
            match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.IGNORECASE | re.DOTALL)
            title = strip_html(match.group(1)) if match else url
        text = strip_html(markup)[:_MAX_TEXT]
        description = (meta_content(markup, name="description") or "").strip()[:500]

        links: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for match in _HREF_RE.findall(markup)[:400]:
            absolute = urljoin(url, match.strip())
            if not absolute.lower().startswith(("http://", "https://")):
                continue
            profile = _profile_from_url(absolute)
            if profile is None:
                if is_aggregator_url(absolute):
                    platform, ident = Platform.WEB, _clean_identifier(
                        urlparse(absolute).path.strip("/").split("/")[0]
                    )
                    if not ident:
                        continue
                    profile = (platform, ident)
                else:
                    continue
            platform, ident = profile
            key = (platform.value, ident)
            if key in seen:
                continue
            seen.add(key)
            links.append(
                {
                    "url": absolute,
                    "platform": platform.value,
                    "username": ident,
                    "aggregator": "1" if is_aggregator_url(absolute) else "0",
                }
            )
            if len(links) >= _MAX_LINKS:
                break

        # Ham metinde/JSON gömülü URL'ler (linktree uygulama gövdesi).
        for raw_url in _RAW_URL_RE.findall(markup):
            absolute = html.unescape(raw_url)
            absolute = re.split(r'["\'<>\s]', absolute)[0].rstrip(".,);]")
            profile = _profile_from_url(absolute)
            if profile is None:
                continue
            platform, ident = profile
            key = (platform.value, ident)
            if key in seen:
                continue
            seen.add(key)
            links.append(
                {
                    "url": absolute,
                    "platform": platform.value,
                    "username": ident,
                    "aggregator": "1" if is_aggregator_url(absolute) else "0",
                }
            )
            if len(links) >= _MAX_LINKS:
                break

        # Toplayıcı sayfasının kendi öneri/şablon profillerini ele; yalnızca
        # hedefin dış platform bağlantıları kalsın.
        page_aggregator = _aggregator_domain(url)
        if page_aggregator:
            links = [
                item
                for item in links
                if _aggregator_domain(str(item.get("url") or "")) != page_aggregator
            ]

        email_text = "" if page_aggregator else text
        emails = list(
            dict.fromkeys(match.lower() for match in _EMAIL_RE.findall(email_text))
        )[:20]
        for match in re.findall(r'mailto:([^"\'? >&]+)', markup, flags=re.IGNORECASE):
            email = match.casefold()
            if "@" in email and email not in emails:
                emails.append(email)
        emails = emails[:20]
        phones: list[str] = []
        for match in _PHONE_RE.findall(text):
            digits = re.sub(r"\D", "", match)
            if 9 <= len(digits) <= 15 and match.strip() not in phones:
                phones.append(match.strip())
        phones = phones[:10]

        digest = hashlib.sha256(raw).hexdigest()
        evidence = self.evidence_store.store_bytes(
            raw, source_url=url, kind="html", http_status=response.status_code
        )
        return [
            Observation(
                id=str(uuid.uuid4()),
                connector=self.name,
                platform=Platform.WEB,
                kind=ObservationKind.PROFILE,
                subject=url,
                url=url,
                tier=self.tier,
                data={
                    "url": url,
                    "title": title[:300],
                    "description": description,
                    "text": text,
                    "links": links,
                    "emails": emails,
                    "phones": phones,
                    "content_hash": digest,
                },
                evidence=[evidence],
            )
        ]

    # ------------------------------------------------------------------
    # Normalize
    # ------------------------------------------------------------------
    def normalize(self, obs: Observation) -> tuple[list[Entity], list[Edge]]:
        if obs.error or obs.blocked or not obs.data:
            return [], []
        url = str(obs.data.get("url") or obs.url or "")
        title = str(obs.data.get("title") or url)[:300]
        text = str(obs.data.get("text") or "")[:2000]
        links = [item for item in obs.data.get("links") or [] if isinstance(item, dict)]
        emails = [str(item) for item in obs.data.get("emails") or []]

        page_id = make_entity_id(Platform.WEB.value, url or title)
        page_entity = Entity(
            id=page_id,
            type=EntityType.ACCOUNT,
            platform=Platform.WEB,
            label=title,
            url=url or None,
            attributes={
                "name": title,
                "bio": text,
                "links": [str(item.get("url")) for item in links if item.get("url")],
                "public_email": emails[0] if emails else None,
                "detection": self.detection,
                "kind": "aggregator_page",
            },
            evidence_ids=[ev.id for ev in obs.evidence],
        )

        entities: list[Entity] = [page_entity]
        edges: list[Edge] = []
        social: dict[str, str] = {}
        for item in links:
            platform_value = str(item.get("platform") or "").lower()
            username = _clean_identifier(str(item.get("username") or ""))
            link_url = str(item.get("url") or "")
            if not platform_value or not username:
                continue
            try:
                platform = Platform(platform_value)
            except ValueError:
                platform = Platform.WEB
            if platform == Platform.WEB:
                entity_id = make_entity_id(Platform.WEB.value, link_url)
                is_nested_aggregator = str(item.get("aggregator") or "") == "1"
                entities.append(
                    Entity(
                        id=entity_id,
                        type=EntityType.ACCOUNT,
                        platform=Platform.WEB,
                        label=username,
                        url=link_url or None,
                        attributes={
                            "username": username,
                            "link_url": link_url,
                            "aggregator": is_nested_aggregator,
                            "social_web": not is_nested_aggregator,
                            "detection": self.detection,
                        },
                    )
                )
            else:
                social.setdefault(platform.value, username)
                entity_id = make_entity_id(platform.value, username)
                entities.append(
                    Entity(
                        id=entity_id,
                        type=EntityType.ACCOUNT,
                        platform=platform,
                        label=f"@{username}",
                        url=link_url or _profile_url(platform, username),
                        attributes={
                            "username": username,
                            "profile_url": link_url,
                            "source_url": url,
                            "links": [link_url] if link_url else [],
                            "detection": self.detection,
                        },
                    )
                )
            edges.append(
                Edge(
                    id=short_edge_id("e:cl", page_id, platform_value, username),
                    type=EdgeType.CROSS_LINK,
                    source_entity_id=page_id,
                    target_entity_id=entity_id,
                    # Bağlantı sayfası doğrudan kullanıcı tarafından yayımlandığı
                    # için güven yüksektir; yine de "confirmed" değildir.
                    confidence=0.85,
                    reason=f"Aggregator sayfası {url} içinde {platform_value} profili",
                    evidence_ids=[ev.id for ev in obs.evidence],
                )
            )

        if social:
            page_entity.attributes["social"] = social
        return entities, edges

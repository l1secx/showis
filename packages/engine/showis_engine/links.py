"""Bio/link metinlerinden platform profili bağlantılarını çıkarır.

Cross-link çıkarımı (Bölüm 7.4/S2-4): bir profilin bio'sundaki bağlantılardan
platform + kullanıcı adı eşleştirilir, CROSS_LINK edge'leri üretilir ve avatar
dosyalarının birebir sha256 eşleşmesi SAME_PERSON sinyali olarak kullanılır.
Biyometrik analiz YAPILMAZ — yalnızca dosya hash'i karşılaştırılır.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass

from PIL import Image
from showis_contracts import Edge, EdgeType, Entity, Platform

from .http import DEFAULT_UA, HttpClient
from .policy import PolicyViolationError, guard_url

logger = logging.getLogger("showis.links")

_TERMINATOR = r"(?:[/?#\s\"'<>)\]]|$)"
_GITHUB_RE = re.compile(
    r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)" + _TERMINATOR,
    re.IGNORECASE,
)
_THM_RE = re.compile(r"tryhackme\.com/p/([A-Za-z0-9_.-]+)", re.IGNORECASE)
_HTB_RE = re.compile(r"(?:app\.|www\.)?hackthebox\.com/profile/([A-Za-z0-9_-]+)", re.IGNORECASE)
_TWITTER_RE = re.compile(
    r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})" + _TERMINATOR, re.IGNORECASE
)
_LINKEDIN_RE = re.compile(
    r"linkedin\.com/in/([A-Za-z0-9_-]+)" + _TERMINATOR, re.IGNORECASE
)
_TELEGRAM_RE = re.compile(r"t\.me/([A-Za-z0-9_]{3,})" + _TERMINATOR, re.IGNORECASE)

_IDENT = r"([A-Za-z0-9][A-Za-z0-9_.~-]{0,63})"


def _dom(path: str) -> re.Pattern[str]:
    """domain + path biciminde profil regex'i uretir."""
    return re.compile(path + _TERMINATOR, re.IGNORECASE)


_PLATFORM_RES: dict[Platform, re.Pattern[str]] = {
    Platform.GITHUB: _GITHUB_RE,
    Platform.GITLAB: _dom(rf"gitlab\.com/{_IDENT}"),
    Platform.TRYHACKME: _THM_RE,
    Platform.HACKTHEBOX: _HTB_RE,
    Platform.BITBUCKET: _dom(rf"bitbucket\.org/{_IDENT}"),
    Platform.CODEBERG: _dom(rf"codeberg\.org/{_IDENT}"),
    Platform.REDDIT: _dom(rf"(?:www\.|old\.)?reddit\.com/user/{_IDENT}"),
    Platform.KEYBASE: _dom(rf"keybase\.io/{_IDENT}"),
    Platform.DEVTO: _dom(rf"dev\.to/{_IDENT}"),
    Platform.MEDIUM: _dom(rf"medium\.com/@{_IDENT}"),
    Platform.HACKERNEWS: _dom(rf"news\.ycombinator\.com/user\?id={_IDENT}"),
    Platform.NPM: _dom(rf"(?:www\.)?npmjs\.com/~{_IDENT}"),
    Platform.PYPI: _dom(rf"pypi\.org/user/{_IDENT}"),
    Platform.DOCKERHUB: _dom(rf"hub\.docker\.com/u/{_IDENT}"),
    Platform.CHESS: _dom(rf"chess\.com/member/{_IDENT}"),
    Platform.STEAM: _dom(rf"steamcommunity\.com/(?:id|profiles)/{_IDENT}"),
    Platform.GRAVATAR: _dom(rf"gravatar\.com/{_IDENT}"),
    Platform.YOUTUBE: _dom(rf"(?:www\.)?youtube\.com/@{_IDENT}"),
    Platform.SOUNDCLOUD: _dom(rf"soundcloud\.com/{_IDENT}"),
    Platform.CODEPEN: _dom(rf"codepen\.io/{_IDENT}"),
    Platform.CODEWARS: _dom(rf"codewars\.com/users/{_IDENT}"),
    Platform.BEHANCE: _dom(rf"(?:www\.)?behance\.net/{_IDENT}"),
    Platform.DRIBBBLE: _dom(rf"dribbble\.com/{_IDENT}"),
    Platform.PINTEREST: _dom(rf"(?:www\.)?pinterest\.\w+/{_IDENT}"),
    Platform.GOODREADS: _dom(rf"(?:www\.)?goodreads\.com/{_IDENT}"),
    Platform.MYANIMELIST: _dom(rf"myanimelist\.net/profile/{_IDENT}"),
    Platform.LETTERBOXD: _dom(rf"letterboxd\.com/{_IDENT}"),
    Platform.VK: _dom(rf"(?:www\.)?vk\.com/{_IDENT}"),
    Platform.ABOUTME: _dom(rf"about\.me/{_IDENT}"),
    Platform.LINKTREE: _dom(rf"linktr\.ee/{_IDENT}"),
    Platform.TELEGRAM: _dom(rf"t\.me/{_IDENT}"),
    Platform.LICHESS: _dom(rf"lichess\.org/@/{_IDENT}"),
    Platform.HACKERRANK: _dom(rf"hackerrank\.com/profile/{_IDENT}"),
    Platform.DUOLINGO: _dom(rf"duolingo\.com/profile/{_IDENT}"),
    Platform.TUMBLR: _dom(r"([A-Za-z0-9_-]+)\.tumblr\.com"),
    Platform.ITCHIO: _dom(r"([A-Za-z0-9_-]+)\.itch\.io"),
    Platform.SUBSTACK: _dom(r"([A-Za-z0-9_-]+)\.substack\.com"),
    Platform.DEVIANTART: _dom(rf"(?:www\.)?deviantart\.com/{_IDENT}"),
    Platform.WATTPAD: _dom(rf"(?:www\.)?wattpad\.com/user/{_IDENT}"),
    Platform.FLICKR: _dom(rf"(?:www\.)?flickr\.com/people/{_IDENT}"),
    Platform.MUSICBRAINZ: _dom(rf"(?:www\.)?musicbrainz\.org/user/{_IDENT}"),
    Platform.MASTODON: _dom(rf"(?:www\.)?mastodon\.social/@{_IDENT}"),
    Platform.PIXELFED: _dom(rf"(?:www\.)?pixelfed\.social/{_IDENT}"),
    Platform.RUBYGEMS: _dom(rf"(?:www\.)?rubygems\.org/profiles/{_IDENT}"),
    Platform.NUGET: _dom(rf"(?:www\.)?nuget\.org/profiles/{_IDENT}"),
    Platform.PACKAGIST: _dom(rf"(?:www\.)?packagist\.org/users/{_IDENT}"),
    Platform.NINEGAG: _dom(rf"(?:www\.)?9gag\.com/u/{_IDENT}"),
    Platform.SPEAKERDECK: _dom(rf"(?:www\.)?speakerdeck\.com/{_IDENT}"),
    Platform.VSMARKET: _dom(rf"(?:www\.)?marketplace\.visualstudio\.com/publishers/{_IDENT}"),
    Platform.BLUESKY: _dom(rf"(?:www\.)?bsky\.app/profile/{_IDENT}"),
    Platform.HUGGINGFACE: _dom(rf"(?:www\.)?huggingface\.co/{_IDENT}"),
    Platform.KITSU: _dom(rf"(?:www\.)?kitsu\.io/users/{_IDENT}"),
    Platform.WORDPRESS: _dom(r"([A-Za-z0-9_-]+)\.wordpress\.com"),
    Platform.LEETCODE: _dom(rf"(?:www\.)?leetcode\.com/(?:u/)?{_IDENT}"),
    Platform.CODEFORCES: _dom(rf"(?:www\.)?codeforces\.com/profile/{_IDENT}"),
    Platform.REPLIT: _dom(rf"(?:www\.)?replit\.com/@{_IDENT}"),
    Platform.DEVPOST: _dom(rf"(?:www\.)?devpost\.com/{_IDENT}"),
    Platform.PATREON: _dom(rf"(?:www\.)?patreon\.com/{_IDENT}"),
    Platform.VIMEO: _dom(rf"(?:www\.)?vimeo\.com/{_IDENT}"),
    Platform.BANDCAMP: _dom(r"([A-Za-z0-9_-]+)\.bandcamp\.com"),
    Platform.DAILYMOTION: _dom(rf"(?:www\.)?dailymotion\.com/{_IDENT}"),
    Platform.SCRATCH: _dom(rf"(?:www\.)?scratch\.mit\.edu/users/{_IDENT}"),
}

_URL_TEMPLATES: dict[Platform, str] = {
    Platform.GITHUB: "https://github.com/{i}",
    Platform.GITLAB: "https://gitlab.com/{i}",
    Platform.TRYHACKME: "https://tryhackme.com/p/{i}",
    Platform.HACKTHEBOX: "https://app.hackthebox.com/profile/{i}",
    Platform.BITBUCKET: "https://bitbucket.org/{i}",
    Platform.CODEBERG: "https://codeberg.org/{i}",
    Platform.REDDIT: "https://www.reddit.com/user/{i}",
    Platform.KEYBASE: "https://keybase.io/{i}",
    Platform.DEVTO: "https://dev.to/{i}",
    Platform.MEDIUM: "https://medium.com/@{i}",
    Platform.HACKERNEWS: "https://news.ycombinator.com/user?id={i}",
    Platform.NPM: "https://www.npmjs.com/~{i}",
    Platform.PYPI: "https://pypi.org/user/{i}",
    Platform.DOCKERHUB: "https://hub.docker.com/u/{i}",
    Platform.CHESS: "https://www.chess.com/member/{i}",
    Platform.GRAVATAR: "https://gravatar.com/{i}",
    Platform.DUOLINGO: "https://www.duolingo.com/profile/{i}",
    Platform.STEAM: "https://steamcommunity.com/id/{i}",
    Platform.YOUTUBE: "https://www.youtube.com/@{i}",
    Platform.SOUNDCLOUD: "https://soundcloud.com/{i}",
    Platform.CODEPEN: "https://codepen.io/{i}",
    Platform.CODEWARS: "https://www.codewars.com/users/{i}",
    Platform.BEHANCE: "https://www.behance.net/{i}",
    Platform.DRIBBBLE: "https://dribbble.com/{i}",
    Platform.PINTEREST: "https://www.pinterest.com/{i}/",
    Platform.GOODREADS: "https://www.goodreads.com/{i}",
    Platform.MYANIMELIST: "https://myanimelist.net/profile/{i}",
    Platform.LETTERBOXD: "https://letterboxd.com/{i}/",
    Platform.VK: "https://vk.com/{i}",
    Platform.ABOUTME: "https://about.me/{i}",
    Platform.LINKTREE: "https://linktr.ee/{i}",
    Platform.TELEGRAM: "https://t.me/{i}",
    Platform.LICHESS: "https://lichess.org/@/{i}",
    Platform.HACKERRANK: "https://www.hackerrank.com/profile/{i}",
    Platform.TUMBLR: "https://{i}.tumblr.com",
    Platform.ITCHIO: "https://{i}.itch.io",
    Platform.SUBSTACK: "https://{i}.substack.com",
    Platform.DEVIANTART: "https://www.deviantart.com/{i}",
    Platform.WATTPAD: "https://www.wattpad.com/user/{i}",
    Platform.FLICKR: "https://www.flickr.com/people/{i}/",
    Platform.MUSICBRAINZ: "https://musicbrainz.org/user/{i}",
    Platform.MASTODON: "https://mastodon.social/@{i}",
    Platform.PIXELFED: "https://pixelfed.social/{i}",
    Platform.RUBYGEMS: "https://rubygems.org/profiles/{i}",
    Platform.NUGET: "https://www.nuget.org/profiles/{i}",
    Platform.PACKAGIST: "https://packagist.org/users/{i}/",
    Platform.NINEGAG: "https://9gag.com/u/{i}",
    Platform.SPEAKERDECK: "https://speakerdeck.com/{i}",
    Platform.VSMARKET: "https://marketplace.visualstudio.com/publishers/{i}",
    Platform.BLUESKY: "https://bsky.app/profile/{i}",
    Platform.HUGGINGFACE: "https://huggingface.co/{i}",
    Platform.KITSU: "https://kitsu.io/users/{i}",
    Platform.WORDPRESS: "https://{i}.wordpress.com",
    Platform.LEETCODE: "https://leetcode.com/u/{i}",
    Platform.CODEFORCES: "https://codeforces.com/profile/{i}",
    Platform.REPLIT: "https://replit.com/@{i}",
    Platform.DEVPOST: "https://devpost.com/{i}",
    Platform.PATREON: "https://www.patreon.com/{i}",
    Platform.VIMEO: "https://vimeo.com/{i}",
    Platform.BANDCAMP: "https://{i}.bandcamp.com",
    Platform.DAILYMOTION: "https://www.dailymotion.com/{i}",
    Platform.SCRATCH: "https://scratch.mit.edu/users/{i}",
}

_GITHUB_RESERVED = {
    "features",
    "pricing",
    "marketplace",
    "topics",
    "search",
    "explore",
    "about",
    "orgs",
    "login",
    "signup",
    "settings",
    "collections",
    "trending",
}

_PLATFORM_PATTERNS: dict[Platform, re.Pattern[str]] = _PLATFORM_RES

_SOCIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "twitter": _TWITTER_RE,
    "linkedin": _LINKEDIN_RE,
    "telegram": _TELEGRAM_RE,
}

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


@dataclass(frozen=True)
class ProfileLink:
    platform: Platform
    identifier: str
    url: str


def _canonical_url(platform: Platform, identifier: str) -> str:
    ident = identifier.strip().strip("/")
    template = _URL_TEMPLATES.get(platform)
    if template:
        return template.format(i=ident)
    return ident


def extract_profile_links(text: str) -> list[ProfileLink]:
    """Serbest metin/HTML içindeki platform profil bağlantılarını çıkarır."""
    if not text:
        return []

    links: list[ProfileLink] = []
    seen: set[tuple[Platform, str]] = set()

    for platform, pattern in _PLATFORM_PATTERNS.items():
        for match in pattern.finditer(text):
            ident = match.group(1)
            if platform == Platform.GITHUB and ident.lower() in _GITHUB_RESERVED:
                continue
            key = (platform, ident.lower())
            if key in seen:
                continue
            seen.add(key)
            links.append(
                ProfileLink(
                    platform=platform,
                    identifier=ident,
                    url=_canonical_url(platform, ident),
                )
            )
    return links


def extract_social_links(text: str) -> dict[str, str]:
    """Twitter/LinkedIn/Telegram bağlantılarını {platform: identifier} döndürür."""
    result: dict[str, str] = {}
    for name, pattern in _SOCIAL_PATTERNS.items():
        m = pattern.search(text or "")
        if m:
            result[name] = m.group(1)
    return result


def urls_in_text(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def profile_links_from_attributes(attributes: dict) -> list[ProfileLink]:
    """Entity attributes içindeki links/blog/github_link vb. alanları tarar."""
    collected: list[str] = []
    links = attributes.get("links")
    if isinstance(links, list):
        collected.extend(str(item) for item in links)
    for key in ("blog", "github_link", "url", "website"):
        val = attributes.get(key)
        if isinstance(val, str) and val:
            collected.append(val)
    # Tüm metni birleştirip tek seferde çıkar (dedupe içeride)
    blob = "\n".join(collected)
    return extract_profile_links(blob)


def entity_profile_key(entity: Entity) -> tuple[Platform, str] | None:
    """Entity'nin (platform, identifier) anahtarını döndürür."""
    ident = entity.attributes.get("username") or entity.attributes.get("login")
    if not ident:
        # label "@user" veya "user" olabilir
        ident = entity.label.lstrip("@")
    if not ident:
        return None
    # HTB'de label profil id olabilir
    return (entity.platform, str(ident).lower())


def build_cross_link_edges(entity: Entity, entities: list[Entity]) -> list[Edge]:
    """Bir entity'nin bio linkleri başka entity'lerle eşleşiyorsa CROSS_LINK edge üretir."""
    edges: list[Edge] = []
    links = profile_links_from_attributes(entity.attributes or {})
    if not links:
        return edges

    known: dict[tuple[Platform, str], str] = {}
    for other in entities:
        key = entity_profile_key(other)
        if key and other.id != entity.id:
            known[key] = other.id

    for link in links:
        target_id = known.get((link.platform, link.identifier.lower()))
        # Hedef henüz entity olarak yoksa deterministik id ile edge üret
        target_entity_id = target_id or f"{link.platform.value}:{link.identifier}"
        edges.append(
            Edge(
                id=f"edge:cross_link:{entity.id}:{link.platform.value}:{link.identifier}",
                type=EdgeType.CROSS_LINK,
                source_entity_id=entity.id,
                target_entity_id=target_entity_id,
                confidence=0.9,
                reason=f"bio/link alanında {link.platform.value} profili: {link.url}",
                evidence_ids=list(entity.evidence_ids),
            )
        )
    return edges


def compute_image_dhash(image_bytes: bytes, hash_size: int = 8) -> str | None:
    """Görsel için 64-bit fark karması (dHash) üretir.

    Görsel gri tonlamaya çevrilip (hash_size+1, hash_size) boyutuna ölçeklenir;
    komşu piksellerin parlaklık farkı bit dizisine dönüştürülür.
    Yeniden boyutlandırılmış veya sıkıştırılmış aynı profil fotoğrafları
    düşük Hamming mesafesi ile tespit edilir.
    """
    if not image_bytes:
        return None
    try:
        with Image.open(io.BytesIO(image_bytes)) as raw_img:
            processed_img = raw_img.convert("L").resize(
                (hash_size + 1, hash_size), Image.Resampling.LANCZOS
            )
            pixels = processed_img.tobytes()
            diff = []
            width = hash_size + 1
            for row in range(hash_size):
                row_start = row * width
                for col in range(hash_size):
                    left = pixels[row_start + col]
                    right = pixels[row_start + col + 1]
                    diff.append(1 if left > right else 0)
            val = 0
            for bit in diff:
                val = (val << 1) | bit
            return f"{val:0{hash_size * hash_size // 4}x}"
    except Exception as exc:
        logger.debug("dHash üretilemedi: %s", exc)
        return None


def hamming_distance(hex1: str, hex2: str) -> int:
    """İki hex dHash arasındaki Hamming mesafesini (farklı bit sayısı) hesaplar."""
    if not hex1 or not hex2:
        return 999
    try:
        val1 = int(hex1, 16)
        val2 = int(hex2, 16)
        return (val1 ^ val2).bit_count()
    except (ValueError, TypeError):
        return 999


# Düz renk / boş görsellerin dHash değerleri (yanlış eşleşmeleri engeller)
_GENERIC_AVATAR_DHASHES: set[str] = {
    "0000000000000000",
    "ffffffffffffffff",
}


def build_avatar_match_edges(
    entities: list[Entity], max_dhash_distance: int = 6
) -> list[Edge]:
    """Aynı veya görsel olarak çok benzer avatar'a sahip entity'ler için SAME_PERSON edge üretir.

    1. Exact SHA-256 eşleşmesi (birebir dosya) -> confidence 0.85
    2. Perceptual dHash eşleşmesi (Hamming mesafesi <= max_dhash_distance) -> confidence 0.75
    """
    edges: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()

    # 1. Exact sha256 eşleşmeleri
    by_sha: dict[str, list[Entity]] = {}
    for ent in entities:
        sha = (ent.attributes or {}).get("avatar_sha256")
        if sha:
            by_sha.setdefault(sha, []).append(ent)

    for sha, group in by_sha.items():
        platforms = {g.platform for g in group}
        if len(platforms) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.platform == b.platform:
                    continue
                pair = (min(a.id, b.id), max(a.id, b.id))
                seen_pairs.add(pair)
                edges.append(
                    Edge(
                        id=f"edge:same_person:avatar_sha:{a.id}:{b.id}",
                        type=EdgeType.SAME_PERSON,
                        source_entity_id=a.id,
                        target_entity_id=b.id,
                        confidence=0.85,
                        reason=f"avatar_exact_hash eşleşmesi: {sha[:16]}…",
                        evidence_ids=list(set(a.evidence_ids) | set(b.evidence_ids)),
                    )
                )

    # 2. Perceptual dHash görsel benzerlik eşleşmeleri
    dhash_entities = [
        ent
        for ent in entities
        if (ent.attributes or {}).get("avatar_dhash")
        and (ent.attributes or {}).get("avatar_dhash") not in _GENERIC_AVATAR_DHASHES
    ]

    for i in range(len(dhash_entities)):
        for j in range(i + 1, len(dhash_entities)):
            a, b = dhash_entities[i], dhash_entities[j]
            if a.platform == b.platform:
                continue
            pair = (min(a.id, b.id), max(a.id, b.id))
            if pair in seen_pairs:
                continue  # Zaten exact sha ile bağlandı

            hash_a = a.attributes["avatar_dhash"]
            hash_b = b.attributes["avatar_dhash"]
            dist = hamming_distance(hash_a, hash_b)
            if dist <= max_dhash_distance:
                seen_pairs.add(pair)
                conf = round(0.80 - (dist * 0.02), 2)  # dist=0 -> 0.80, dist=6 -> 0.68
                match_reason = (
                    f"avatar görsel benzerliği (dHash mesafe={dist}): "
                    f"{hash_a[:8]}… ~ {hash_b[:8]}…"
                )
                edges.append(
                    Edge(
                        id=f"edge:same_person:avatar_dhash:{a.id}:{b.id}",
                        type=EdgeType.SAME_PERSON,
                        source_entity_id=a.id,
                        target_entity_id=b.id,
                        confidence=conf,
                        reason=match_reason,
                        evidence_ids=list(set(a.evidence_ids) | set(b.evidence_ids)),
                    )
                )

    return edges


async def fetch_avatar_hashes(
    avatar_url: str | None,
    http_client: HttpClient | None = None,
    *,
    max_bytes: int = 2_000_000,
) -> tuple[str | None, str | None]:
    """Avatar dosyasını indirip (sha256, dhash) demetini döndürür. Hata durumunda (None, None)."""
    if not avatar_url:
        return (None, None)
    try:
        guard_url(avatar_url)
    except PolicyViolationError:
        return (None, None)

    client = http_client or HttpClient(enable_ssrf_guard=True)
    try:
        resp = await client.get(
            avatar_url, headers={"User-Agent": DEFAULT_UA}, check_robots=False
        )
        if resp.status_code != 200:
            return (None, None)
        data = resp.content[:max_bytes]
        sha = hashlib.sha256(data).hexdigest()
        dhash = compute_image_dhash(data)
        return (sha, dhash)
    except Exception as exc:  # pragma: no cover - ağ hataları
        logger.debug("Avatar hash alınamadı (%s): %s", avatar_url, exc)
        return (None, None)


async def fetch_avatar_sha256(
    avatar_url: str | None,
    http_client: HttpClient | None = None,
    *,
    max_bytes: int = 2_000_000,
) -> str | None:
    """Avatar dosyasını indirip birebir sha256'sını döndürür (geriye dönük uyumluluk)."""
    sha, _ = await fetch_avatar_hashes(avatar_url, http_client, max_bytes=max_bytes)
    return sha

import asyncio
import logging
import os
import re
from typing import Any

from showis_contracts import Platform, QueryPlan, SearchQuery, Target, VariantSet
from showis_llm import LLMClient, render_prompt

from .policy import PolicyViolationError, guard_query, guard_target

logger = logging.getLogger("showis.planner")


# Varyant aramasinda kullanilan platform terimleri (tek kaynak, tekrarsiz).
_VARIANT_OR_TERMS: tuple[str, ...] = (
    "github",
    "gitlab",
    "tryhackme",
    "hackthebox",
    "linkedin",
    "twitter",
    "instagram",
    "facebook",
    "tiktok",
    "threads",
    "mastodon",
    "bluesky",
    "huggingface",
    "wordpress",
    "kaggle",
    "bitbucket",
    "codeberg",
    "hashnode",
    "behance",
    "dribbble",
    "codepen",
    "codewars",
    "chess",
    "steam",
    "gravatar",
    "wattpad",
    "flickr",
    "musicbrainz",
    "pixelfed",
    "rubygems",
    "nuget",
    "packagist",
    "9gag",
    "speakerdeck",
    "vs-marketplace",
    "youtube",
    "medium",
    "substack",
    "deviantart",
    "soundcloud",
    "myanimelist",
    "letterboxd",
    "goodreads",
    "about.me",
    "linktree",
    "telegram",
    "vk",
    "lichess",
    "duolingo",
    "tumblr",
    "itch.io",
    "dev.to",
    "hackernews",
    "npm",
    "pypi",
    "dockerhub",
    "keybase",
    "reddit",
    "kitsu",
    "pinterest",
    "rootme",
)

#: `"{terim}" <site>` biçiminde üretilen platform sorguları. İsim ve nick yolu
#: **aynı** tabloyu kullanır; tek fark gerekçe metnidir ("tam isim araması" →
#: "nick araması"). Böylece handle modu isim yoluyla aynı platform kapsamına
#: sahip olur (tek kaynak, iki dil).
#: (site operatörü, platform, isim yolu gerekçesi, öncelik)
_PLATFORM_QUERIES: tuple[tuple[str, Platform, str, int], ...] = (
    # --- Derin connector'lar (Tier A) ---
    ("site:github.com", Platform.GITHUB, "GitHub üzerinde tam isim araması", 1),
    ("site:gitlab.com", Platform.GITLAB, "GitLab üzerinde tam isim araması", 1),
    ("site:keybase.io", Platform.KEYBASE, "Keybase üzerinde tam isim araması", 1),
    ("site:reddit.com", Platform.REDDIT, "Reddit üzerinde tam isim araması", 1),
    ("site:dev.to", Platform.DEVTO, "Dev.to üzerinde tam isim araması", 1),
    ("site:news.ycombinator.com", Platform.HACKERNEWS, "HackerNews üzerinde tam isim araması", 1),
    ("site:npmjs.com", Platform.NPM, "NPM üzerinde tam isim araması", 1),
    ("site:pypi.org", Platform.PYPI, "PyPI üzerinde tam isim araması", 1),
    ("site:hub.docker.com", Platform.DOCKERHUB, "DockerHub üzerinde tam isim araması", 1),
    ("site:chess.com", Platform.CHESS, "Chess.com üzerinde tam isim araması", 1),
    ("site:steamcommunity.com", Platform.STEAM, "Steam üzerinde tam isim araması", 1),
    ("site:gravatar.com", Platform.GRAVATAR, "Gravatar üzerinde tam isim araması", 1),
    # --- Bağlantı toplayıcıları (bio link sayfaları) ---
    ("site:linktr.ee", Platform.LINKTREE, "Linktree bağlantı sayfası araması", 1),
    ("site:about.me", Platform.ABOUTME, "about.me profil araması", 2),
    ("site:beacons.ai", Platform.BEACONS, "Beacons bağlantı sayfası araması", 2),
    ("site:carrd.co", Platform.CARDD, "Carrd bağlantı sayfası araması", 2),
    ("site:bio.link", Platform.WEB, "bio.link bağlantı sayfası araması", 2),
    ("site:linkr.bio", Platform.WEB, "linkr.bio bağlantı sayfası araması", 2),
    # --- CTF / hacking platformlari ---
    ("site:tryhackme.com", Platform.TRYHACKME, "TryHackMe üzerinde tam isim araması", 2),
    ("site:hackthebox.com", Platform.HACKTHEBOX, "HackTheBox üzerinde tam isim araması", 2),
    ("site:root-me.org", Platform.ROOTME, "Root-ME üzerinde tam isim araması", 2),
    # --- Sosyal platformlar (login duvarı nedeniyle daha düşük öncelik) ---
    (
        "site:linkedin.com",
        Platform.WEB,
        "LinkedIn üzerinde tam isim araması (herkese açık sayfalar)",
        2,
    ),
    ("site:twitter.com OR site:x.com", Platform.WEB, "Twitter/X üzerinde tam isim araması", 2),
    ("site:instagram.com", Platform.INSTAGRAM, "Instagram üzerinde tam isim araması", 2),
    ("site:facebook.com", Platform.WEB, "Facebook üzerinde tam isim araması", 2),
    ("site:tiktok.com", Platform.TIKTOK, "TikTok üzerinde tam isim araması", 2),
    ("site:twitch.tv", Platform.TWITCH, "Twitch üzerinde tam isim araması", 2),
    ("site:mixcloud.com", Platform.MIXCLOUD, "Mixcloud üzerinde tam isim araması", 2),
    ("site:threads.net", Platform.WEB, "Threads üzerinde tam isim araması", 2),
    # --- Gelistirme / teknoloji platformlari ---
    ("site:medium.com", Platform.MEDIUM, "Medium üzerinde tam isim araması", 2),
    ("site:behance.net", Platform.BEHANCE, "Behance üzerinde tam isim araması", 2),
    ("site:dribbble.com", Platform.DRIBBBLE, "Dribbble üzerinde tam isim araması", 2),
    ("site:codepen.io", Platform.CODEPEN, "CodePen üzerinde tam isim araması", 2),
    ("site:codewars.com", Platform.CODEWARS, "Codewars üzerinde tam isim araması", 2),
    ("site:kaggle.com", Platform.KAGGLE, "Kaggle üzerinde tam isim araması", 2),
    ("site:bitbucket.org", Platform.BITBUCKET, "Bitbucket üzerinde tam isim araması", 2),
    ("site:codeberg.org", Platform.CODEBERG, "Codeberg üzerinde tam isim araması", 2),
    ("site:hashnode.com", Platform.HASHNODE, "Hashnode üzerinde tam isim araması", 2),
    # --- Paket / kod depolari ---
    ("site:rubygems.org", Platform.RUBYGEMS, "RubyGems üzerinde tam isim araması", 2),
    ("site:nuget.org", Platform.NUGET, "NuGet üzerinde tam isim araması", 2),
    ("site:packagist.org", Platform.PACKAGIST, "Packagist üzerinde tam isim araması", 2),
    (
        "site:marketplace.visualstudio.com",
        Platform.VSMARKET,
        "VS Marketplace üzerinde tam isim araması",
        2,
    ),
    # --- Oyun / medya / icerik platformlari ---
    ("site:youtube.com", Platform.YOUTUBE, "YouTube üzerinde tam isim araması", 2),
    ("site:myanimelist.net", Platform.MYANIMELIST, "MyAnimeList üzerinde tam isim araması", 2),
    ("site:letterboxd.com", Platform.LETTERBOXD, "Letterboxd üzerinde tam isim araması", 2),
    ("site:goodreads.com", Platform.GOODREADS, "Goodreads üzerinde tam isim araması", 2),
    ("site:soundcloud.com", Platform.SOUNDCLOUD, "SoundCloud üzerinde tam isim araması", 2),
    ("site:wattpad.com", Platform.WATTPAD, "Wattpad üzerinde tam isim araması", 2),
    ("site:flickr.com", Platform.FLICKR, "Flickr üzerinde tam isim araması", 2),
    ("site:musicbrainz.org", Platform.MUSICBRAINZ, "MusicBrainz üzerinde tam isim araması", 2),
    ("site:9gag.com", Platform.NINEGAG, "9GAG üzerinde tam isim araması", 2),
    ("site:speakerdeck.com", Platform.SPEAKERDECK, "SpeakerDeck üzerinde tam isim araması", 2),
    # --- Fediverse / sosyal (instance-bazli) ---
    (
        "site:mastodon.social",
        Platform.MASTODON,
        "Mastodon (mastodon.social) üzerinde tam isim araması",
        2,
    ),
    (
        "site:pixelfed.social",
        Platform.PIXELFED,
        "Pixelfed (pixelfed.social) üzerinde tam isim araması",
        2,
    ),
    # --- Yeni sosyal API'ler ---
    ("site:bsky.app", Platform.BLUESKY, "Bluesky üzerinde tam isim araması", 2),
    ("site:huggingface.co", Platform.HUGGINGFACE, "HuggingFace üzerinde tam isim araması", 2),
    ("site:kitsu.io", Platform.KITSU, "Kitsu üzerinde tam isim araması", 2),
    ("site:wordpress.com", Platform.WORDPRESS, "WordPress.com blog üzerinde tam isim araması", 2),
)

#: Şablon planının **birincil** blok tavanı (mevcut davranış korunur). Nick ve
#: platform-bağımsız sorgular bu tavanın dışında eklenir; böylece 50 satırlık
#: site tablosu tavanı tek başına doldursa bile nick sorguları kaybolmaz.
_MAX_TEMPLATE_QUERIES = 50


def _nickname_term(target: Target) -> str:
    """Hedefin nick'ini sorguya güvenle konabilecek biçime getirir.

    Baştaki ``@`` atılır; tırnak/operatör/negasyon :func:`sanitize_hint` ile
    nötralize edilir. Normalize sonucu boşsa "" döner (o zaman hiç nick sorgusu
    üretilmez).
    """
    raw = str(target.nickname or "").strip().lstrip("@").strip()
    if not raw:
        return ""
    return sanitize_hint(raw)


def _site_queries(term: str, *, nickname: bool = False) -> list[SearchQuery]:
    """Platform bazlı `"{terim}" site:...` sorguları.

    ``term`` boşsa hiç sorgu üretilmez: `"" site:github.com` gibi bozuk sorgu
    hiçbir koşulda plana girmez. ``nickname=True`` gerekçe metnini nick diline
    çevirir.
    """
    if not term:
        return []
    queries: list[SearchQuery] = []
    for site, platform, rationale, priority in _PLATFORM_QUERIES:
        queries.append(
            SearchQuery(
                query=f'"{term}" {site}',
                platform=platform,
                rationale=rationale.replace("tam isim", "nick") if nickname else rationale,
                priority=priority,
            )
        )
    return queries


def _agnostic_nick_query(nick: str) -> list[SearchQuery]:
    """Platform-bağımsız geniş nick sorgusu (`"nick"`)."""
    if not nick:
        return []
    return [
        SearchQuery(
            query=f'"{nick}"',
            platform=Platform.WEB,
            rationale="Web genelinde nick araması",
            priority=3,
        )
    ]


def _nickname_queries(nick: str) -> list[SearchQuery]:
    """Nick sorgu bloğu: platform kapsamı + platform-bağımsız geniş sorgu."""
    queries = _site_queries(nick, nickname=True)
    queries.extend(_agnostic_nick_query(nick))
    return queries


def _generic_name_queries(fullname: str) -> list[SearchQuery]:
    """Domain-bağımsız isim + anahtar sözcük sorguları."""
    if not fullname:
        return []
    return [
        SearchQuery(
            query=f'"{fullname}" {keyword}',
            platform=Platform.WEB,
            rationale=f"Web genelinde isim ve {keyword} anahtarı araması",
            priority=3,
        )
        for keyword in ("github", "developer", "engineer")
    ]


def _combined_name_queries(ad: str, soyad: str) -> list[SearchQuery]:
    """Birleşik ad-soyad (username) biçimleri — yalnız iki alan da doluysa."""
    if not ad or not soyad:
        return []
    patterns = (
        (f"{ad}{soyad}", "Birleşik ad soyad araması"),
        (f"{ad}.{soyad}", "Noktalı ad soyad araması"),
        (f"{ad}_{soyad}", "Alt çizgili ad soyad araması"),
        (f"{ad}-{soyad}", "Tireli ad soyad araması"),
    )
    return [
        SearchQuery(
            query=f'"{pattern}" github',
            platform=Platform.WEB,
            rationale=rationale,
            priority=3,
        )
        for pattern, rationale in patterns
    ]


def _identity_join_queries(ad: str, soyad: str, nick: str) -> list[SearchQuery]:
    """Seed kullanıcı adı ile ad/soyadı aynı sorguda eşleştirir.

    Kullanıcı bir seed hesap verdiğinde isim ve kullanıcı adı kombinasyonları
    en yüksek önceliği alır; tek başına nick araması linktree gibi toplayıcı
    sayfaları kaçırabilir.
    """
    if not nick:
        return []
    fullname = f"{ad} {soyad}".strip()
    queries: list[SearchQuery] = []
    if fullname:
        queries.extend(
            [
                SearchQuery(
                    query=f'"{nick}" "{fullname}"',
                    platform=Platform.WEB,
                    rationale="Seed kullanıcı adı ve tam isim eşleştirmesi",
                    priority=1,
                ),
                SearchQuery(
                    query=f'"{fullname}" "{nick}"',
                    platform=Platform.WEB,
                    rationale="Tam isim ve seed kullanıcı adı eşleştirmesi",
                    priority=1,
                ),
            ]
        )
    for term in (ad, soyad):
        if term and len(term) > 2:
            queries.append(
                SearchQuery(
                    query=f'"{nick}" "{term}"',
                    platform=Platform.WEB,
                    rationale="Seed kullanıcı adı ve isim parçası eşleştirmesi",
                    priority=2,
                )
            )
    queries.append(
        SearchQuery(
            query=f'"{nick}" linktree OR about.me OR beacons',
            platform=Platform.WEB,
            rationale="Seed hesabının bağlantı toplayıcı sayfaları",
            priority=1,
        )
    )
    return queries


def _profile_queries(ad: str) -> list[SearchQuery]:
    """CTF platformlarında profil araması (yalnız ad varsa: `""` üretilmez)."""
    if not ad:
        return []
    return [
        SearchQuery(
            query=f'site:tryhackme.com/p "{ad}"',
            platform=Platform.TRYHACKME,
            rationale="TryHackMe profil arama (isim)",
            priority=4,
        ),
        SearchQuery(
            query=f'site:hackthebox.com/profile "{ad}"',
            platform=Platform.HACKTHEBOX,
            rationale="HackTheBox profil arama (isim)",
            priority=4,
        ),
    ]


def _variant_queries(variants: VariantSet | None) -> list[SearchQuery]:
    """Varyant kullanıcı adları için geniş OR sorguları."""
    if not variants or not variants.variants:
        return []
    return [
        SearchQuery(
            query=f'"{v.username}" ({" OR ".join(_VARIANT_OR_TERMS)})',
            platform=Platform.WEB,
            rationale=f"Varyant kullanıcı adı araması: {v.username}",
            priority=4,
        )
        for v in variants.variants[:15]
    ]


def _finalize_queries(queries: list[SearchQuery]) -> list[SearchQuery]:
    """Şablon çıktısının **tek** son kapısı (T-377/SEC-10).

    Her sorgu :func:`finalize_query` ile süzülür; boş/çok kısa sorgu
    (``ValueError``) veya yasak desen (``PolicyViolationError``) o sorguyu
    düşürür, kalanlar yaşar. ``sanitize_query`` arama operatörlerini koruduğu
    için mevcut şablon sorguları bayt bayt aynı kalır.
    """
    finalized: list[SearchQuery] = []
    for q in queries:
        try:
            cleaned = finalize_query(q.query)
        except (ValueError, PolicyViolationError):
            logger.debug("Şablon sorgusu elendi: %r", q.query)
            continue
        finalized.append(q.model_copy(update={"query": cleaned}))
    return finalized


def _enforce_handle_queries(target: Target, queries: list[SearchQuery]) -> list[SearchQuery]:
    """Handle modu garantisini **kod** düzeyinde uygular (prompt'a güvenmez).

    ``target.kind == "handle"`` iken nick dışı her sorgu düşer: LLM tam isim
    uydursa da, isim kaynaklı varyant sorgusu karışsa da plana giremez. Nick
    normalize edilemiyorsa (boş) handle modunda aranacak bir şey yoktur → plan
    boş döner. İsim modunda filtre yok (davranış değişmez).
    """
    if target.kind != "handle":
        return queries
    nick = _nickname_term(target)
    if not nick:
        return []
    needle = nick.casefold()
    # Havuz sorguları kullanıcı girdisidir; handle modunda da korunur.
    # Diğer sorgular nick içermek zorundadır (isim uydurma engeli).
    kept = [
        q
        for q in queries
        if needle in q.query.casefold() or q.rationale.startswith("Havuz kaydı")
    ]
    if len(kept) != len(queries):
        logger.debug(
            "Handle modunda nick dışı %d sorgu elendi", len(queries) - len(kept)
        )
    return kept


def _finalize_plan(target: Target, queries: list[SearchQuery]) -> QueryPlan:
    """Her iki yolun (şablon + LLM) paylaştığı son adım: kapı + handle filtresi."""
    return QueryPlan(queries=_enforce_handle_queries(target, _finalize_queries(queries)))


def _pool_queries(pool_items: list[dict[str, Any]] | None) -> list[SearchQuery]:
    """Havuzdaki kullanıcı girdilerinden deterministik arama sorguları.

    Havuz kayıtları doğrudan kullanıcı girdisidir; bu yüzden operatör
    nötralizasyonu (`sanitize_hint`) uygulanır. Aynı değer iki kez eklenmez.
    `event` kayıtlarında tarih/yer/kurum bağlamı da sorguya katılır.
    """
    if not pool_items:
        return []
    queries: list[SearchQuery] = []
    seen: set[str] = set()
    for item in pool_items[:24]:
        kind = str(item.get("kind") or "").strip().lower()
        raw_value = str(item.get("value") or "").strip()
        if not raw_value or kind in {"note"}:
            continue
        rationale = f"Havuz kaydı ({kind}) için doğrulama araması"
        context = ""
        if kind == "event":
            attrs = item.get("attributes")
            if isinstance(attrs, dict):
                date = str(attrs.get("date") or "").strip()
                date_hint = date[:4] if len(date) >= 4 and date[:4].isdigit() else ""
                context = sanitize_hint(
                    " ".join(
                        part
                        for part in (
                            date_hint,
                            str(attrs.get("place") or "").strip(),
                            str(attrs.get("organization") or "").strip(),
                        )
                        if part
                    ),
                    max_length=60,
                )
            rationale = "Havuz olayı (tarih/yer/kurum) için doğrulama araması"
        value = sanitize_hint(raw_value, max_length=120)
        if not value or len(value) < 3:
            continue
        query_text = f'"{value}"' + (f" {context}" if context else "")
        key = query_text.casefold()
        if key in seen:
            continue
        seen.add(key)
        queries.append(
            SearchQuery(
                query=query_text,
                platform=Platform.WEB,
                rationale=rationale,
                priority=2,
            )
        )
    return queries


def build_template_fallback_queries(
    target: Target,
    variants: VariantSet | None = None,
    pool_items: list[dict[str, Any]] | None = None,
) -> QueryPlan:
    """LLM mevcut olmadığında veya hata verdiğinde deterministik şablon sorguları üretir."""
    # SEC-07 / K-3: şablon yolu doğrudan çağrıldığında da hedef denetimi zorunludur
    target = guard_target(target)
    if target.review_required:
        raise PolicyViolationError(
            "Hedef inceleme gerektiriyor: reşit olmayan kişi sinyali (AUP md.7)."
        )

    fullname = f"{target.first_name} {target.last_name}".strip()
    ad = target.first_name.strip()
    soyad = target.last_name.strip()
    nick = _nickname_term(target)
    # Handle modu: hedef nick ile tanımlı ya da ad/soyad hiç yok. Bu modda
    # `"" site:github.com` gibi bozuk isim sorgusu üretilmez.
    handle_mode = target.kind == "handle" or not fullname

    queries: list[SearchQuery] = []
    if target.kind in {"email", "phone", "domain", "ip"}:
        identifier = sanitize_hint(target.identifier)
        if identifier:
            queries.append(
                SearchQuery(
                    query=f'"{identifier}"',
                    platform=Platform.WEB,
                    rationale=f"{target.kind} kimliği için web araması",
                    priority=1,
                )
            )
        queries.extend(_pool_queries(pool_items))
        return _finalize_plan(target, queries)
    if handle_mode:
        # `_site_queries` 50 satırlık tabloyu üretir; tavan tek başına dolduğu
        # için varyantlar burada da plana giremez (isim yolundaki davranışın aynısı).
        queries.extend(_site_queries(nick, nickname=True))
        queries.extend(_variant_queries(variants))
        queries = queries[:_MAX_TEMPLATE_QUERIES]
        # Platform-bağımsız geniş sorgu tavan dışında: tablo tavanı doldursa
        # bile handle modunun son çare kapsamı kaybolmaz. Handle modunda isim
        # alanları yok sayılır; yalnız seed kullanıcı adı kullanılır.
        queries.extend(_identity_join_queries("", "", nick))
        queries.extend(_agnostic_nick_query(nick))
        queries.extend(_pool_queries(pool_items))
    else:
        queries.extend(_site_queries(fullname))
        queries.extend(_generic_name_queries(fullname))
        queries.extend(_combined_name_queries(ad, soyad))
        queries.extend(_profile_queries(ad))
        queries.extend(_variant_queries(variants))
        queries = queries[:_MAX_TEMPLATE_QUERIES]
        # İsim sorguları aynen korunur; nick ve havuz sorguları sona eklenir.
        queries.extend(_identity_join_queries(ad, soyad, nick))
        queries.extend(_nickname_queries(nick))
        queries.extend(_pool_queries(pool_items))

    return _finalize_plan(target, queries)


# ---------------------------------------------------------------------------
# T-377 (SEC-10): sorgu metni tek kaynaktan doğrulanır
# ---------------------------------------------------------------------------

#: İpucu alanı için azami uzunluk (aşırı uzun metin = token bombası)
MAX_HINT_LENGTH = 80
#: Toplam sorgu uzunluğu sınırı
MAX_QUERY_LENGTH = 200

#: Arama motoru operatörleri: `site:`, `filetype:`, `inurl:` …
_OPERATOR_RE = re.compile(
    r"\b(site|filetype|inurl|intitle|intext|allinurl|allintitle|cache|related|link"
    r"|in|repo|language|user|org|topic)\s*:",
    re.IGNORECASE,
)
#: Boolean anahtar sözcükleri (tırnak dışında operatör gibi davranır)
_BOOLEAN_RE = re.compile(r"\b(OR|AND|NOT|NEAR)\b", re.IGNORECASE)
#: Kontrol karakterleri (satır sonu, null, escape…)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
#: Tırnak/yıldız/ters bölü: sorgu sözdizimini kırar
_QUERY_SPECIALS_RE = re.compile(r'["\*\\`]')


def sanitize_hint(value: object, *, max_length: int = MAX_HINT_LENGTH) -> str:
    """İpucu değerini sorguya güvenle konabilecek biçime getirir.

    * kontrol karakterleri atılır (varsa tüm alan reddedilir),
    * `site:`/`filetype:`/`inurl:` gibi operatörler `site_` biçimine nötralize edilir,
    * `OR`/`AND`/`NOT`/`NEAR` küçük harfe indirilir (operatör olmaktan çıkar),
    * tırnak, `*`, ters bölü, backtick boşluğa çevrilir,
    * satır başındaki `-` (negasyon) atılır,
    * boşluklar tekilleştirilir ve uzunluk `max_length` ile kırpılır.

    Boş sonuç → "" (çağıran ipucu eklemez).
    """
    text = str(value or "")
    if not text:
        return ""
    if _CONTROL_RE.search(text):
        logger.debug("Sorgu ipucu reddedildi: kontrol karakteri")
        return ""
    text = _OPERATOR_RE.sub(lambda m: f"{m.group(1)}_", text)
    text = _BOOLEAN_RE.sub(lambda m: m.group(1).lower(), text)
    text = _QUERY_SPECIALS_RE.sub(" ", text)
    text = re.sub(r"(^|\s)-(\S)", r"\1\2", text)  # negasyon operatorunu kaldir
    text = " ".join(text.split())
    return text[:max_length].strip()


def sanitize_query(text: object, *, max_length: int = MAX_QUERY_LENGTH) -> str:
    """Sistem/LLM tarafından yazılmış sorguyu normalize eder.

    Burada arama operatörleri **korunur** (``site:github.com`` meşru ve gerekli);
    yalnız kontrol karakterleri, aşırı uzunluk ve boşluklar düzeltilir.
    Kullanıcı ipuçları için :func:`sanitize_hint` (operatörleri nötralize eder).
    """
    text = str(text or "")
    if _CONTROL_RE.search(text):
        logger.debug("Sorgu reddedildi: kontrol karakteri")
        return ""
    text = " ".join(text.split())
    return text[:max_length].strip()


def finalize_query(text: object, *, max_length: int = MAX_QUERY_LENGTH) -> str:
    """Hem LLM hem şablon yolu için **tek** son kapı (T-377/SEC-10).

    Sanitize eder, boşsa ``ValueError``, yasak desen içeriyorsa
    ``PolicyViolationError`` fırlatır (çağıran o sorguyu atlar).
    """
    cleaned = sanitize_query(text, max_length=max_length)
    if not cleaned or len(cleaned) < 2:
        raise ValueError("boş sorgu")
    return guard_query(cleaned)


#: T-333: planlama için LLM'e ayrılan üst süre (sn). Aşılırsa şablon planı.
PLANNER_TIMEOUT_ENV = "SHOWIS_LLM_PLANNER_TIMEOUT"
DEFAULT_PLANNER_TIMEOUT = 10.0


def _planner_timeout() -> float:
    raw = os.getenv(PLANNER_TIMEOUT_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_PLANNER_TIMEOUT
    try:
        return max(1.0, float(str(raw).strip()))
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan kullanilir", PLANNER_TIMEOUT_ENV, raw)
        return DEFAULT_PLANNER_TIMEOUT


class QueryPlanner:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client
        #: T-333: aynı taramada LLM bir kez başarısız olduysa ikinci kez denenmez.
        self.llm_disabled = False

    async def plan(
        self,
        target: Target,
        variants: VariantSet,
        max_queries: int = 30,
        pool_items: list[dict[str, Any]] | None = None,
    ) -> QueryPlan:
        # SEC-07 (defense in depth): inceleme gerektiren hedef için hiç sorgu
        # üretilmez; hedef kaydı aşılsa bile planlama katmanı durdurur.
        target = guard_target(target)
        if target.review_required:
            raise PolicyViolationError(
                "Hedef inceleme gerektiriyor: reşit olmayan kişi sinyali (AUP md.7)."
            )

        # E-posta/telefon/domain/IP için LLM'e gerek yok: kimlik anahtarı
        # doğrudan aranır, isim varyantı üretilmez.
        if target.kind not in {"name", "handle"}:
            return build_template_fallback_queries(target, variants, pool_items)
        if self.llm_client is None or self.llm_disabled:
            return build_template_fallback_queries(target, variants, pool_items)

        prompt_pool: list[str] = []
        for item in (pool_items or [])[:30]:
            line = f"[{item.get('kind')}] {item.get('value')}"
            attrs = item.get("attributes")
            if item.get("kind") == "event" and isinstance(attrs, dict):
                context = " | ".join(
                    f"{key}={attrs.get(key)}"
                    for key in ("date", "place", "organization")
                    if attrs.get(key)
                )
                if context:
                    line = f"{line} ({context})"
            prompt_pool.append(line)

        try:
            prompt_content = render_prompt(
                "query_planner",
                first_name=target.first_name,
                last_name=target.last_name,
                hints=target.hints,
                variants=[v.username for v in variants.variants[:10]],
                # Faz 1 = A: handle modunda LLM isim uydurmasın.
                kind=target.kind,
                nickname=target.nickname,
                pool_items=prompt_pool,
            )

            # T-333: LLM erişilemezse ~90 sn beklemek yerine hızla şablona düş.
            plan = await asyncio.wait_for(
                self.llm_client.complete(
                    messages=[{"role": "user", "content": prompt_content}],
                    schema=QueryPlan,
                    temperature=0.0,
                ),
                timeout=_planner_timeout(),
            )

            if isinstance(plan, QueryPlan) and plan.queries:
                # LLM çıktısı da aynı kapıdan geçer: TEK kötü sorgu tüm planı
                # (ve devre kesiciyi) düşürmez, yalnızca o sorgu atlanır.
                guarded = _finalize_plan(
                    target, plan.queries[:max_queries] + _pool_queries(pool_items)
                )
                if guarded.queries:
                    return guarded

        except Exception as e:
            # Aynı tarama ömründe ikinci deneme yok (devre kesici + bu bayrak).
            self.llm_disabled = True
            logger.warning(
                "LLM Planner başarısız oldu (%s), şablon fallback'e geçiliyor",
                type(e).__name__,
            )

        return build_template_fallback_queries(target, variants, pool_items)

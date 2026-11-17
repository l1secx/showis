import asyncio
import logging
import os
import re

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
    ("site:instagram.com", Platform.WEB, "Instagram üzerinde tam isim araması", 2),
    ("site:facebook.com", Platform.WEB, "Facebook üzerinde tam isim araması", 2),
    ("site:tiktok.com", Platform.WEB, "TikTok üzerinde tam isim araması", 2),
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
    düşürür. ``sanitize_query`` arama operatörlerini koruduğu için mevcut şablon
    sorguları bayt bayt aynı kalır.
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


def build_template_fallback_queries(
    target: Target, variants: VariantSet | None = None
) -> QueryPlan:
    """LLM mevcut olmadığında veya hata verdiğinde deterministik şablon sorguları üretir.

    30+ platform kapsamı: GitHub, GitLab, Keybase, Reddit, Dev.to, HackerNews, NPM,
    PyPI, DockerHub, Chess.com, Steam, Gravatar, Wattpad, Flickr, MusicBrainz, Mastodon,
    Pixelfed, RubyGems, NuGet, Packagist, 9GAG, SpeakerDeck, VS Marketplace, Bluesky,
    HuggingFace, Kitsu, WordPress.com, LinkedIn, Twitter/X, Instagram, Facebook, TikTok,
    Threads, Medium, YouTube, Behance, Dribbble, Pinterest, MyAnimeList, Letterboxd,
    Goodreads, SoundCloud, CodePen, CodeWars, Behance, Dribbble, About.me, Linktree,
    Telegram, VK, Lichess, Duolingo, Tumblr, itch.io, Substack, DeviantArt, Bitbucket,
    Codeberg, Hashnode, Kaggle, RootMe.

    Handle modu (``target.kind == "handle"`` ya da ad/soyad birlikte boş): isim
    biçimli sorgu **üretilmez**; aynı platform kapsamı nick ile kurulur
    (``"{nick}" site:github.com``, …) ve platform-bağımsız ``"{nick}"`` sorgusu
    eklenir. İsim modunda nick varsa mevcut isim sorguları **aynen** korunur,
    nick sorguları listenin sonuna eklenir.
    """
    fullname = f"{target.first_name} {target.last_name}".strip()
    ad = target.first_name.strip()
    soyad = target.last_name.strip()
    nick = _nickname_term(target)
    # Handle modu: hedef nick ile tanımlı ya da ad/soyad hiç yok. Bu modda
    # `"" site:github.com` gibi bozuk isim sorgusu üretilmez.
    handle_mode = target.kind == "handle" or not fullname

    queries: list[SearchQuery] = []
    if handle_mode:
        # `_site_queries` 50 satırlık tabloyu üretir; tavan tek başına dolduğu
        # için varyantlar burada da plana giremez (isim yolundaki davranışın aynısı).
        queries.extend(_site_queries(nick, nickname=True))
        queries.extend(_variant_queries(variants))
        queries = queries[:_MAX_TEMPLATE_QUERIES]
        # Platform-bağımsız geniş sorgu tavan dışında: tablo tavanı doldursa
        # bile handle modunun son çare kapsamı kaybolmaz.
        queries.extend(_agnostic_nick_query(nick))
    else:
        queries.extend(_site_queries(fullname))
        queries.extend(_generic_name_queries(fullname))
        queries.extend(_combined_name_queries(ad, soyad))
        queries.extend(_profile_queries(ad))
        queries.extend(_variant_queries(variants))
        queries = queries[:_MAX_TEMPLATE_QUERIES]
        # İsim sorguları aynen korunur; nick sorguları listenin sonuna eklenir.
        queries.extend(_nickname_queries(nick))

    return QueryPlan(queries=_finalize_queries(queries))


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
    ) -> QueryPlan:
        # SEC-07 (defense in depth): inceleme gerektiren hedef için hiç sorgu
        # üretilmez; hedef kaydı aşılsa bile planlama katmanı durdurur.
        target = guard_target(target)
        if target.review_required:
            raise PolicyViolationError(
                "Hedef inceleme gerektiriyor: reşit olmayan kişi sinyali (AUP md.7)."
            )

        if self.llm_client is None or self.llm_disabled:
            return build_template_fallback_queries(target, variants)

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
                # LLM ciktisi da yasak desen kontrolunden gecer (hints enjeksiyonu)
                guarded = [
                    q.model_copy(update={"query": finalize_query(q.query)})
                    for q in plan.queries[:max_queries]
                ]
                return QueryPlan(queries=guarded)

        except Exception as e:
            # Aynı tarama ömründe ikinci deneme yok (devre kesici + bu bayrak).
            self.llm_disabled = True
            logger.warning(
                "LLM Planner başarısız oldu (%s), şablon fallback'e geçiliyor",
                type(e).__name__,
            )

        return build_template_fallback_queries(target, variants)

import logging

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
    """
    fullname = f"{target.first_name} {target.last_name}".strip()
    ad = target.first_name.strip()
    soyad = target.last_name.strip()

    queries: list[SearchQuery] = []

    # --- Derin connector'lar (Tier A) ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:github.com',
                platform=Platform.GITHUB,
                rationale="GitHub üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:gitlab.com',
                platform=Platform.GITLAB,
                rationale="GitLab üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:keybase.io',
                platform=Platform.KEYBASE,
                rationale="Keybase üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:reddit.com',
                platform=Platform.REDDIT,
                rationale="Reddit üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:dev.to',
                platform=Platform.DEVTO,
                rationale="Dev.to üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:news.ycombinator.com',
                platform=Platform.HACKERNEWS,
                rationale="HackerNews üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:npmjs.com',
                platform=Platform.NPM,
                rationale="NPM üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:pypi.org',
                platform=Platform.PYPI,
                rationale="PyPI üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:hub.docker.com',
                platform=Platform.DOCKERHUB,
                rationale="DockerHub üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:chess.com',
                platform=Platform.CHESS,
                rationale="Chess.com üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:steamcommunity.com',
                platform=Platform.STEAM,
                rationale="Steam üzerinde tam isim araması",
                priority=1,
            ),
            SearchQuery(
                query=f'"{fullname}" site:gravatar.com',
                platform=Platform.GRAVATAR,
                rationale="Gravatar üzerinde tam isim araması",
                priority=1,
            ),
        ]
    )

    # --- CTF / hacking platformlari ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:tryhackme.com',
                platform=Platform.TRYHACKME,
                rationale="TryHackMe üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:hackthebox.com',
                platform=Platform.HACKTHEBOX,
                rationale="HackTheBox üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:root-me.org',
                platform=Platform.ROOTME,
                rationale="Root-ME üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Sosyal platformlar (login duvarı nedeniyle daha düşük öncelik ---
    #    ancak isim araması bazen halka açık sayfalara dönebilir)
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:linkedin.com',
                platform=Platform.WEB,
                rationale="LinkedIn üzerinde tam isim araması (herkese açık sayfalar)",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:twitter.com OR site:x.com',
                platform=Platform.WEB,
                rationale="Twitter/X üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:instagram.com',
                platform=Platform.WEB,
                rationale="Instagram üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:facebook.com',
                platform=Platform.WEB,
                rationale="Facebook üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:tiktok.com',
                platform=Platform.WEB,
                rationale="TikTok üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:threads.net',
                platform=Platform.WEB,
                rationale="Threads üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Gelistirme / teknoloji platformlari ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:medium.com',
                platform=Platform.MEDIUM,
                rationale="Medium üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:behance.net',
                platform=Platform.BEHANCE,
                rationale="Behance üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:dribbble.com',
                platform=Platform.DRIBBBLE,
                rationale="Dribbble üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:codepen.io',
                platform=Platform.CODEPEN,
                rationale="CodePen üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:codewars.com',
                platform=Platform.CODEWARS,
                rationale="Codewars üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:kaggle.com',
                platform=Platform.KAGGLE,
                rationale="Kaggle üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:bitbucket.org',
                platform=Platform.BITBUCKET,
                rationale="Bitbucket üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:codeberg.org',
                platform=Platform.CODEBERG,
                rationale="Codeberg üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:hashnode.com',
                platform=Platform.HASHNODE,
                rationale="Hashnode üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Paket / kod depolari ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:rubygems.org',
                platform=Platform.RUBYGEMS,
                rationale="RubyGems üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:nuget.org',
                platform=Platform.NUGET,
                rationale="NuGet üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:packagist.org',
                platform=Platform.PACKAGIST,
                rationale="Packagist üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:marketplace.visualstudio.com',
                platform=Platform.VSMARKET,
                rationale="VS Marketplace üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Oyun / medya / icerik platformlari ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:youtube.com',
                platform=Platform.YOUTUBE,
                rationale="YouTube üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:myanimelist.net',
                platform=Platform.MYANIMELIST,
                rationale="MyAnimeList üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:letterboxd.com',
                platform=Platform.LETTERBOXD,
                rationale="Letterboxd üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:goodreads.com',
                platform=Platform.GOODREADS,
                rationale="Goodreads üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:soundcloud.com',
                platform=Platform.SOUNDCLOUD,
                rationale="SoundCloud üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:wattpad.com',
                platform=Platform.WATTPAD,
                rationale="Wattpad üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:flickr.com',
                platform=Platform.FLICKR,
                rationale="Flickr üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:musicbrainz.org',
                platform=Platform.MUSICBRAINZ,
                rationale="MusicBrainz üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:9gag.com',
                platform=Platform.NINEGAG,
                rationale="9GAG üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:speakerdeck.com',
                platform=Platform.SPEAKERDECK,
                rationale="SpeakerDeck üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Fediverse / sosyal (instance-bazli) ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:mastodon.social',
                platform=Platform.MASTODON,
                rationale="Mastodon (mastodon.social) üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:pixelfed.social',
                platform=Platform.PIXELFED,
                rationale="Pixelfed (pixelfed.social) üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Yeni sosyal API'ler ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" site:bsky.app',
                platform=Platform.BLUESKY,
                rationale="Bluesky üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:huggingface.co',
                platform=Platform.HUGGINGFACE,
                rationale="HuggingFace üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:kitsu.io',
                platform=Platform.KITSU,
                rationale="Kitsu üzerinde tam isim araması",
                priority=2,
            ),
            SearchQuery(
                query=f'"{fullname}" site:wordpress.com',
                platform=Platform.WORDPRESS,
                rationale="WordPress.com blog üzerinde tam isim araması",
                priority=2,
            ),
        ]
    )

    # --- Genel web (domain-agnotic) ---
    queries.extend(
        [
            SearchQuery(
                query=f'"{fullname}" github',
                platform=Platform.WEB,
                rationale="Web genelinde isim ve github anahtarı araması",
                priority=3,
            ),
            SearchQuery(
                query=f'"{fullname}" developer',
                platform=Platform.WEB,
                rationale="Web genelinde isim ve developer anahtarı araması",
                priority=3,
            ),
            SearchQuery(
                query=f'"{fullname}" engineer',
                platform=Platform.WEB,
                rationale="Web genelinde isim ve engineer anahtarı araması",
                priority=3,
            ),
        ]
    )

    # --- Username varyant aramasi ---
    if ad and soyad:
        queries.extend(
            [
                SearchQuery(
                    query=f'"{ad}{soyad}" github',
                    platform=Platform.WEB,
                    rationale="Birleşik ad soyad araması",
                    priority=3,
                ),
                SearchQuery(
                    query=f'"{ad}.{soyad}" github',
                    platform=Platform.WEB,
                    rationale="Noktalı ad soyad araması",
                    priority=3,
                ),
                SearchQuery(
                    query=f'"{ad}_{soyad}" github',
                    platform=Platform.WEB,
                    rationale="Alt çizgili ad soyad araması",
                    priority=3,
                ),
                SearchQuery(
                    query=f'"{ad}-{soyad}" github',
                    platform=Platform.WEB,
                    rationale="Tireli ad soyad araması",
                    priority=3,
                ),
            ]
        )

    # --- Kullanici adi bazli aramalar (sadece ad kullanildiginda) ---
    queries.extend(
        [
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
    )

    # --- Varyantlar (username bazli) ---
    if variants and variants.variants:
        for v in variants.variants[:15]:
            queries.append(
                SearchQuery(
                    query=f'"{v.username}" ({" OR ".join(_VARIANT_OR_TERMS)})',
                    platform=Platform.WEB,
                    rationale=f"Varyant kullanıcı adı araması: {v.username}",
                    priority=4,
                )
            )

    return QueryPlan(queries=queries[:50])


class QueryPlanner:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

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

        if self.llm_client is None:
            return build_template_fallback_queries(target, variants)

        try:
            prompt_content = render_prompt(
                "query_planner",
                first_name=target.first_name,
                last_name=target.last_name,
                hints=target.hints,
                variants=[v.username for v in variants.variants[:10]],
            )

            plan = await self.llm_client.complete(
                messages=[{"role": "user", "content": prompt_content}],
                schema=QueryPlan,
                temperature=0.0,
            )

            if isinstance(plan, QueryPlan) and plan.queries:
                # LLM ciktisi da yasak desen kontrolunden gecer (hints enjeksiyonu)
                guarded = [
                    q.model_copy(update={"query": guard_query(q.query)})
                    for q in plan.queries[:max_queries]
                ]
                return QueryPlan(queries=guarded)

        except Exception as e:
            logger.warning("LLM Planner başarısız oldu, şablon fallback'e geçiliyor: %s", e)

        return build_template_fallback_queries(target, variants)

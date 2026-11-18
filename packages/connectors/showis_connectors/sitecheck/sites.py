"""Username-enumeration site veritabani.

Her kayit, bir platformun kamuya acik profil/hesap varlik kontrolunu
tanimlar. Tespit birden cok sinyalin kombinasyonuyla yapilir:

- ``absent_statuses``: bu HTTP kodlari "hesap yok" demektir (vars. 404,410)
- ``expect_status``: hesap icin beklenen kod (vars. 200)
- ``json_path``: JSON yanitinda izlenecek nokta yol ("" = kok).
  Dolu/deger varsa hesap vardir; bos/null/liste ise yok.
- ``present_regex`` / ``absent_regex``: govde uzerinde calisan isaretler.
  ``absent_regex`` eslesirse hesap yok; ``present_regex`` eslesirse var.

Hicbir alan eslesmezse sonuc ``unknown`` olur — yanlis pozitif uretmemek
icin "var" karari ancak pozitif sinyal (status 200 + json dolu veya
present_regex) ile verilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from showis_contracts import Platform


@dataclass(frozen=True)
class SiteSpec:
    key: str
    name: str
    platform: Platform
    #: {u} yerine kullanici adi konulan profil adresi
    url_template: str
    #: Hesap yok sayilan HTTP durumlari
    absent_statuses: tuple[int, ...] = (404, 410)
    #: Hesap icin beklenen durum (None -> herhangi bir 2xx)
    expect_status: int | None = 200
    #: JSON yanitinda hesap-varlik kontrolu yapilacak nokta yolu
    #: ("" veya "." = kok nesne; liste/dict icin truthy kontrol)
    json_path: str | None = None
    #: Govdede hesap-var oldugunu gosteren isaret
    present_regex: str | None = None
    #: Govdede hesap-yok oldugunu gosteren isaret
    absent_regex: str | None = None
    #: present_regex eslesmezse sonuc
    #: False -> "unknown" (kararsiz), True -> "absent" (isaret zorunlu)
    no_match_is_absent: bool = False
    #: Bu site icin ek HTTP basliklari (orn. API Accept)
    headers: dict[str, str] = field(default_factory=dict)
    #: Bu siteye istek atilirken robots.txt kontrolunu atla
    #: (yalnizca herkese acik, oturumsuz API uçlari icin)
    skip_robots: bool = False
    #: T-365: kullanici adi icin opsiyonel regex (varsayilan: [A-Za-z0-9][A-Za-z0-9._-]{0,63}).
    #: Yalniz platform gercekten farkli bir karakter kumesi kabul ediyorsa doldurulur.
    username_pattern: str | None = None
    notes: str = ""
    #: Turetilmis veri kaynagi kategorisi (orn. WMN ``cat``); elle yazilan
    #: spec'lerde bos. Tarama politikasi (orn. NSFW dislama) bunu okur.
    category: str = ""


def _json_site(
    key: str,
    name: str,
    platform: Platform,
    url_template: str,
    *,
    path: str = "",
    absent_statuses: tuple[int, ...] = (404, 410),
    skip_robots: bool = False,
    notes: str = "",
) -> SiteSpec:
    return SiteSpec(
        key=key,
        name=name,
        platform=platform,
        url_template=url_template,
        absent_statuses=absent_statuses,
        json_path=path,
        expect_status=200,
        headers={"Accept": "application/json"},
        skip_robots=skip_robots,
        notes=notes,
    )


SITES: list[SiteSpec] = [
    # ------------------------------------------------------------------
    # Gelistirme / teknoloji platformlari
    # (gitlab, npm, pypi, dockerhub, devto, chess, reddit, keybase, steam,
    #  gravatar, hackernews ayri derin connector'larda — burada tekrarlanmaz)
    # ------------------------------------------------------------------
    SiteSpec(
        "bitbucket",
        "Bitbucket",
        Platform.BITBUCKET,
        "https://bitbucket.org/{u}/",
    ),
    SiteSpec(
        "codeberg",
        "Codeberg",
        Platform.CODEBERG,
        "https://codeberg.org/{u}",
    ),
    SiteSpec(
        "codepen",
        "CodePen",
        Platform.CODEPEN,
        "https://codepen.io/{u}",
    ),
    _json_site(
        "codewars",
        "Codewars",
        Platform.CODEWARS,
        "https://www.codewars.com/api/v1/users/{u}",
        notes="Public v1 users API",
    ),
    # ------------------------------------------------------------------
    # Sosyal / icerik platformlari
    # ------------------------------------------------------------------
    SiteSpec(
        "youtube",
        "YouTube",
        Platform.YOUTUBE,
        "https://www.youtube.com/@{u}",
    ),
    SiteSpec(
        "medium",
        "Medium",
        Platform.MEDIUM,
        "https://medium.com/@{u}",
        absent_regex=r"doesn.t exist|page you were looking for",
        notes="Hesap yok sayfasi 200 donebilir",
    ),
    SiteSpec(
        "substack",
        "Substack",
        Platform.SUBSTACK,
        "https://{u}.substack.com",
        notes="Subdomain; DNS hatasi 'unknown' sayilir",
    ),
    SiteSpec(
        "deviantart",
        "DeviantArt",
        Platform.DEVIANTART,
        "https://www.deviantart.com/{u}",
    ),
    SiteSpec(
        "behance",
        "Behance",
        Platform.BEHANCE,
        "https://www.behance.net/{u}",
    ),
    SiteSpec(
        "dribbble",
        "Dribbble",
        Platform.DRIBBBLE,
        "https://dribbble.com/{u}",
    ),
    SiteSpec(
        "pinterest",
        "Pinterest",
        Platform.PINTEREST,
        "https://www.pinterest.com/{u}/",
        absent_regex=r"[Uu]ser not found",
        notes="Hesap yoksa 'User not found' metni icerir",
    ),
    SiteSpec(
        "tumblr",
        "Tumblr",
        Platform.TUMBLR,
        "https://{u}.tumblr.com",
        absent_regex=r"[Tt]here.?s nothing here|blog doesn.t exist",
    ),
    SiteSpec(
        "about.me",
        "About.me",
        Platform.ABOUTME,
        "https://about.me/{u}",
    ),
    SiteSpec(
        "linktr.ee",
        "Linktree",
        Platform.LINKTREE,
        "https://linktr.ee/{u}",
    ),
    SiteSpec(
        "myanimelist",
        "MyAnimeList",
        Platform.MYANIMELIST,
        "https://myanimelist.net/profile/{u}",
    ),
    SiteSpec(
        "letterboxd",
        "Letterboxd",
        Platform.LETTERBOXD,
        "https://letterboxd.com/{u}/",
    ),
    SiteSpec(
        "goodreads",
        "Goodreads",
        Platform.GOODREADS,
        "https://www.goodreads.com/{u}",
    ),
    SiteSpec(
        "soundcloud",
        "SoundCloud",
        Platform.SOUNDCLOUD,
        "https://soundcloud.com/{u}",
        absent_regex=r"Profile not found|not found",
        notes="404 veya 'Profile not found' = hesap yok",
    ),
    SiteSpec(
        "itch.io",
        "itch.io",
        Platform.ITCHIO,
        "https://{u}.itch.io",
        notes="Kullanici alt-domain'i",
    ),
    SiteSpec(
        "telegram",
        "Telegram",
        Platform.TELEGRAM,
        "https://t.me/{u}",
        present_regex=r"tgme_page_(?:photo|title|extra|add)",
        no_match_is_absent=True,
        notes="Gecerli kullanici tgme_page blogu icerir; yoksa jenerik sayfa",
    ),
    SiteSpec(
        "vk",
        "VK",
        Platform.VK,
        "https://vk.com/{u}",
        absent_regex=r"Page not found|страница не найдена|такой страницы не существует",
        notes="Cogu profil giris duvari ardinda; dusuk guven",
    ),
    SiteSpec(
        "lichess",
        "Lichess",
        Platform.LICHESS,
        "https://lichess.org/@/{u}",
    ),
    # Not: HackerRank profil sayfalari duz HTTP istemciye "browser is not
    # supported" bot-duvari (HTTP 200) dondurur; yanlis-pozitif uretmemek
    # icin bu listeye alinmadi. Render destekli tarayici gerekir.
    # Not: duolingo (2017-06-30/users) kaldirildi — tarih-damgali, dokumante
    # edilmemis dahili API. bluesky/huggingface/kitsu/wordpress de kaldirildi:
    # publicapi/ altindaki derin connector'larda zaten var (cift istek oluyordu).
    # ------------------------------------------------------------------
    # Ek HTML profil sayfalari (canli dogrulanmis 404/200 davranisi)
    # ------------------------------------------------------------------
    SiteSpec(
        "wattpad",
        "Wattpad",
        Platform.WATTPAD,
        "https://www.wattpad.com/user/{u}",
    ),
    SiteSpec(
        "flickr",
        "Flickr",
        Platform.FLICKR,
        "https://www.flickr.com/people/{u}/",
    ),
    SiteSpec(
        "musicbrainz",
        "MusicBrainz",
        Platform.MUSICBRAINZ,
        "https://musicbrainz.org/user/{u}",
    ),
    SiteSpec(
        "mastodon",
        "Mastodon",
        Platform.MASTODON,
        "https://mastodon.social/@{u}",
        notes="Yalnizca mastodon.social instance'indeki kullanicilar",
    ),
    SiteSpec(
        "pixelfed",
        "Pixelfed",
        Platform.PIXELFED,
        "https://pixelfed.social/{u}",
        notes="Yalnizca pixelfed.social instance'indeki kullanicilar",
    ),
    SiteSpec(
        "rubygems",
        "RubyGems",
        Platform.RUBYGEMS,
        "https://rubygems.org/profiles/{u}",
    ),
    SiteSpec(
        "nuget",
        "NuGet",
        Platform.NUGET,
        "https://www.nuget.org/profiles/{u}",
    ),
    SiteSpec(
        "packagist",
        "Packagist",
        Platform.PACKAGIST,
        "https://packagist.org/users/{u}/",
    ),
    SiteSpec(
        "9gag",
        "9GAG",
        Platform.NINEGAG,
        "https://9gag.com/u/{u}",
    ),
    SiteSpec(
        "speakerdeck",
        "Speaker Deck",
        Platform.SPEAKERDECK,
        "https://speakerdeck.com/{u}",
    ),
    SiteSpec(
        "vsmarket",
        "VS Marketplace",
        Platform.VSMARKET,
        "https://marketplace.visualstudio.com/publishers/{u}",
        absent_statuses=(404, 410, 500),
        notes="Olmayan publisher icin 500 doner; 500 ayrica gecici sunucu hatasi olabilir",
    ),
    SiteSpec(
        "kaggle",
        "Kaggle",
        Platform.KAGGLE,
        "https://www.kaggle.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "hackerrank",
        "HackerRank",
        Platform.HACKERRANK,
        "https://www.hackerrank.com/profile/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "rootme",
        "Root-Me",
        Platform.ROOTME,
        "https://www.root-me.org/{u}",
        absent_statuses=(404, 410),
    ),
    _json_site(
        "duolingo",
        "Duolingo",
        Platform.DUOLINGO,
        "https://www.duolingo.com/2017-06-30/users?username={u}",
        path="users",
        skip_robots=True,
    ),
    SiteSpec(
        "hashnode",
        "Hashnode",
        Platform.HASHNODE,
        "https://hashnode.com/@{u}",
        absent_statuses=(404, 410),
    ),
    _json_site(
        "bluesky",
        "Bluesky",
        Platform.BLUESKY,
        "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={u}.bsky.social",
        path="did",
        skip_robots=True,
    ),
    _json_site(
        "huggingface",
        "HuggingFace",
        Platform.HUGGINGFACE,
        "https://huggingface.co/api/users/{u}/overview",
        path="user",
        skip_robots=True,
    ),
    _json_site(
        "kitsu",
        "Kitsu",
        Platform.KITSU,
        "https://kitsu.io/api/edge/users?filter[slug]={u}",
        path="data",
        skip_robots=True,
    ),
    SiteSpec(
        "wordpress",
        "WordPress",
        Platform.WORDPRESS,
        "https://{u}.wordpress.com",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "lastfm",
        "Last.fm",
        Platform.LASTFM,
        "https://www.last.fm/user/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "leetcode",
        "LeetCode",
        Platform.LEETCODE,
        "https://leetcode.com/u/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "codeforces",
        "Codeforces",
        Platform.CODEFORCES,
        "https://codeforces.com/profile/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "replit",
        "Replit",
        Platform.REPLIT,
        "https://replit.com/@{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "devpost",
        "Devpost",
        Platform.DEVPOST,
        "https://devpost.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "patreon",
        "Patreon",
        Platform.PATREON,
        "https://www.patreon.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "vimeo",
        "Vimeo",
        Platform.VIMEO,
        "https://vimeo.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "bandcamp",
        "Bandcamp",
        Platform.BANDCAMP,
        "https://{u}.bandcamp.com",
        absent_statuses=(404, 410),
        # T-357 canlı doğrulama: olmayan alt alan adı 200 + "Signup | Bandcamp"
        # sayfası döndürüyor (yanlış pozitif) → absent sinyali eklendi.
        absent_regex=r"<title>\s*Signup\s*\|\s*Bandcamp",
    ),
    SiteSpec(
        "dailymotion",
        "Dailymotion",
        Platform.DAILYMOTION,
        "https://www.dailymotion.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "scratch",
        "Scratch",
        Platform.SCRATCH,
        "https://scratch.mit.edu/users/{u}",
        absent_statuses=(404, 410),
    ),

    # ------------------------------------------------------------------
    # T-357/357b: genisletilmis kapsam — canli dogrulanmis herkese acik profiller
    # Kural: login duvari/CAPTCHA arkasindaki platformlar EKLENMEZ; kirilgan
    # present_regex yazilmaz. Yalniz durum-kodu (absent_statuses), dogal "bos"
    # API yaniti (json_path) veya kanitlanmis metin isareti (absent_regex).
    #
    # Canli dogrulamada ELENENLER (spec yazilmadi):
    #   twitch, trello, trakt, anilist, glitch, 500px -> SPA: her kullanici icin 200 kabuk
    #   backloggd -> Cloudflare "not a bot" duvari
    #   etsy, ebay, producthunt, artstation, fandom, fiverr, codesandbox -> 403 bot duvari
    #   beacons -> self-signed TLS; mixcloud, ao3 -> bu agdan baglanti kurulamadi
    # ------------------------------------------------------------------
    SiteSpec(
        "kick",
        "Kick",
        Platform.KICK,
        "https://kick.com/{u}",
        absent_statuses=(404, 410),
        notes="Kanal yoksa 404 (Cloudflare 403 -> unknown)",
    ),
    SiteSpec(
        "kofi",
        "Ko-fi",
        Platform.KOFI,
        "https://ko-fi.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "buymeacoffee",
        "Buy Me a Coffee",
        Platform.BUYMEACOFFEE,
        "https://www.buymeacoffee.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "gumroad",
        "Gumroad",
        Platform.GUMROAD,
        "https://{u}.gumroad.com",
        absent_statuses=(404, 410),
        notes="Alt alan adi; DNS yoksa unknown",
    ),
    SiteSpec(
        "slideshare",
        "SlideShare",
        Platform.SLIDESHARE,
        "https://www.slideshare.net/{u}",
        absent_statuses=(404, 410),
        # T-357b canli dogrulama: olmayan kullanici 200 + "Page no longer exists"
        absent_regex=r"Page no longer exists",
    ),
    SiteSpec(
        "scribd",
        "Scribd",
        Platform.SCRIBD,
        "https://www.scribd.com/{u}",
        absent_statuses=(404, 410),
        notes="Bot korumasi 403 -> unknown",
    ),
    SiteSpec(
        "sourcehut",
        "SourceHut",
        Platform.SOURCEHUT,
        "https://sr.ht/~{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "velog",
        "Velog",
        Platform.VELOG,
        "https://velog.io/@{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "hackerone",
        "HackerOne",
        Platform.HACKERONE,
        "https://hackerone.com/{u}",
        absent_statuses=(404, 410),
        notes="OSINT: guvenlik arastirmacisi kimligi",
    ),
    SiteSpec(
        "bugcrowd",
        "Bugcrowd",
        Platform.BUGCROWD,
        "https://bugcrowd.com/{u}",
        absent_statuses=(404, 410),
    ),
    SiteSpec(
        "carrd",
        "Carrd",
        Platform.CARDD,
        "https://{u}.carrd.co",
        absent_statuses=(404, 410),
        notes="Alt alan adi",
    ),
    SiteSpec(
        "stackblitz",
        "StackBlitz",
        Platform.STACKBLITZ,
        "https://stackblitz.com/@{u}",
        absent_statuses=(404, 410),
    ),
]

#: site key -> spec hizli erisim
SITES_BY_KEY: dict[str, SiteSpec] = {s.key: s for s in SITES}

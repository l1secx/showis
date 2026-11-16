from enum import StrEnum


class RiskTier(StrEnum):
    A = "A"  # resmî API
    B = "B"  # herkese açık sayfa
    C = "C"  # yalnızca arama indeksi


class Platform(StrEnum):
    # --- Derin connector'lar (zengin profil verisi) ---
    GITHUB = "github"
    GITLAB = "gitlab"
    KEYBASE = "keybase"
    REDDIT = "reddit"
    TRYHACKME = "tryhackme"
    HACKTHEBOX = "hackthebox"
    STEAM = "steam"
    GRAVATAR = "gravatar"
    HACKERNEWS = "hackernews"
    NPM = "npm"
    PYPI = "pypi"
    DOCKERHUB = "dockerhub"
    DEVTO = "devto"
    CHESS = "chess"
    LEETCODE = "leetcode"
    CODEFORCES = "codeforces"
    # T-308: anahtarsiz public API connector'lari (yalniz ekleme)
    STACKOVERFLOW = "stackoverflow"
    # --- Site-check (kullanici adi varlik kontrolu) platformlari ---
    BITBUCKET = "bitbucket"
    CODEBERG = "codeberg"
    CODEPEN = "codepen"
    KAGGLE = "kaggle"
    CODEWARS = "codewars"
    ROOTME = "root-me"
    HACKERRANK = "hackerrank"
    DUOLINGO = "duolingo"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    MEDIUM = "medium"
    SUBSTACK = "substack"
    DEVIANTART = "deviantart"
    BEHANCE = "behance"
    DRIBBBLE = "dribbble"
    PINTEREST = "pinterest"
    TUMBLR = "tumblr"
    ABOUTME = "about.me"
    LINKTREE = "linktr.ee"
    MYANIMELIST = "myanimelist"
    LETTERBOXD = "letterboxd"
    GOODREADS = "goodreads"
    LASTFM = "lastfm"
    SOUNDCLOUD = "soundcloud"
    ITCHIO = "itch.io"
    TELEGRAM = "telegram"
    VK = "vk"
    LICHESS = "lichess"
    HASHNODE = "hashnode"
    WORDPRESS = "wordpress"
    BLUESKY = "bluesky"
    HUGGINGFACE = "huggingface"
    KITSU = "kitsu"
    WATTPAD = "wattpad"
    FLICKR = "flickr"
    MUSICBRAINZ = "musicbrainz"
    MASTODON = "mastodon"
    PIXELFED = "pixelfed"
    RUBYGEMS = "rubygems"
    NUGET = "nuget"
    PACKAGIST = "packagist"
    NINEGAG = "9gag"
    VSMARKET = "vs-marketplace"
    SPEAKERDECK = "speakerdeck"
    REPLIT = "replit"
    DEVPOST = "devpost"
    PATREON = "patreon"
    VIMEO = "vimeo"
    BANDCAMP = "bandcamp"
    DAILYMOTION = "dailymotion"
    SCRATCH = "scratch"
    WEB = "web"
    UNKNOWN = "unknown"


class ObservationKind(StrEnum):
    SEARCH_RESULT = "search_result"
    PROFILE = "profile"
    REPOSITORY = "repository"
    COMMIT = "commit"
    EVENT = "event"
    SNIPPET = "snippet"
    BLOCKED = "blocked"


class FindingStatus(StrEnum):
    # LEAD: yalnizca isim/okul benzerligi olan, dogrulanmamis arama sonucu ipucu
    LEAD = "lead"
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class EntityType(StrEnum):
    PERSON = "person"
    ACCOUNT = "account"
    DOMAIN = "domain"
    EMAIL = "email"
    USERNAME = "username"


class EdgeType(StrEnum):
    HAS_ACCOUNT = "has_account"
    CROSS_LINK = "cross_link"
    SAME_PERSON = "same_person"
    MENTIONS = "mentions"
    OWNED_DOMAIN = "owned_domain"

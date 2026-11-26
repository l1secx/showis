import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from showis_contracts import Platform

GITHUB_PATTERN = re.compile(r"^https?://(?:www\.)?github\.com/([a-zA-Z0-9_-]+)/?$", re.IGNORECASE)
THM_PATTERN = re.compile(
    r"^https?://(?:www\.)?tryhackme\.com/p/([a-zA-Z0-9_.-]+)/?$", re.IGNORECASE
)
HTB_PATTERN = re.compile(
    r"^https?://(?:www\.|app\.)?hackthebox\.com/profile/([a-zA-Z0-9_-]+)/?$", re.IGNORECASE
)


def normalize_url(raw_url: str) -> str:
    """URL normalizasyonu: izleme parametrelerini temizler,
    www. kaldırır ve path sonunu standardize eder."""
    if not raw_url:
        return ""

    parsed = urlparse(raw_url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Remove tracking query parameters
    query_params = parse_qsl(parsed.query, keep_blank_values=False)
    filtered_params = [
        (k, v)
        for k, v in query_params
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref")
    ]
    new_query = urlencode(filtered_params)

    path = parsed.path
    if path and path != "/" and path.endswith("/"):
        path = path[:-1]

    normalized = urlunparse((scheme, netloc, path, "", new_query, ""))
    return normalized


def _host_matches(host: str, domain: str) -> bool:
    """Host, domain'in kendisi veya alt alan adı mı? (substring değil)"""
    return host == domain or host.endswith("." + domain)


def extract_platform_from_url(url: str) -> tuple[Platform, str | None]:
    """URL'den platform ve kullanıcı adı / ID çıkarır."""
    norm = normalize_url(url)
    parsed = urlparse(norm)
    host = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    def first() -> str | None:
        return path_parts[0] if path_parts else None

    if _host_matches(host, "github.com"):
        user = first()
        if user and user not in (
            "features", "pricing", "marketplace", "topics", "search",
            "explore", "about", "orgs", "settings", "login", "signup",
        ):
            return Platform.GITHUB, user

    elif _host_matches(host, "gitlab.com"):
        user = first()
        if user and user not in ("explore", "users", "groups", "help", "api"):
            return Platform.GITLAB, user

    elif _host_matches(host, "tryhackme.com"):
        if len(path_parts) >= 2 and path_parts[0] == "p":
            return Platform.TRYHACKME, path_parts[1]

    elif _host_matches(host, "hackthebox.com"):
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.HACKTHEBOX, path_parts[1]

    elif _host_matches(host, "reddit.com"):
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.REDDIT, path_parts[1]

    elif _host_matches(host, "keybase.io"):
        user = first()
        if user and user != "_":
            return Platform.KEYBASE, user

    elif _host_matches(host, "steamcommunity.com"):
        if len(path_parts) >= 2 and path_parts[0] in ("id", "profiles"):
            return Platform.STEAM, path_parts[1]

    elif _host_matches(host, "dev.to") or _host_matches(host, "developers.com"):
        user = first()
        if user and user not in ("api", "enter", "signout", "dashboard"):
            return Platform.DEVTO, user

    elif _host_matches(host, "hub.docker.com"):
        if len(path_parts) >= 2 and path_parts[0] == "u":
            return Platform.DOCKERHUB, path_parts[1]

    elif _host_matches(host, "npmjs.com") or _host_matches(host, "npmjs.org"):
        if len(path_parts) >= 1 and path_parts[0] == "~" and len(path_parts) >= 2:
            return Platform.NPM, path_parts[1]

    elif _host_matches(host, "pypi.org"):
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.PYPI, path_parts[1]

    elif _host_matches(host, "chess.com"):
        if len(path_parts) >= 2 and path_parts[0] in ("member", "player"):
            return Platform.CHESS, path_parts[1]

    elif _host_matches(host, "codewars.com"):
        if len(path_parts) >= 2 and path_parts[0] == "users":
            return Platform.CODEWARS, path_parts[1]

    elif _host_matches(host, "hackerrank.com"):
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.HACKERRANK, path_parts[1]

    elif _host_matches(host, "kaggle.com"):
        user = first()
        if user and user not in ("competitions", "datasets", "code", "forums", "account"):
            return Platform.KAGGLE, user

    elif _host_matches(host, "bitbucket.org"):
        user = first()
        if user and user not in ("repo", "workspace", "get-started"):
            return Platform.BITBUCKET, user

    elif _host_matches(host, "codeberg.org"):
        user = first()
        if user and user not in ("explore", "assets", "api", "user"):
            return Platform.CODEBERG, user

    elif _host_matches(host, "codepen.io"):
        user = first()
        if user and user not in ("pen", "collections", "trending", "search"):
            return Platform.CODEPEN, user

    elif _host_matches(host, "hashnode.com") or _host_matches(host, "hashnode.dev"):
        if path_parts and path_parts[0].startswith("@"):
            return Platform.HASHNODE, path_parts[0][1:]
        user = first()
        if user and user not in ("feed", "blog", "tags", "search"):
            return Platform.HASHNODE, user

    elif _host_matches(host, "instagram.com"):
        user = first()
        if user and user not in (
            "p", "reel", "reels", "stories", "explore", "accounts",
            "direct", "tv", "developer",
        ):
            return Platform.INSTAGRAM, user

    elif _host_matches(host, "tiktok.com"):
        if path_parts and path_parts[0].startswith("@"):
            return Platform.TIKTOK, path_parts[0][1:]

    elif _host_matches(host, "youtube.com") or _host_matches(host, "youtu.be"):
        user = first()
        if user and user.startswith("@"):
            return Platform.YOUTUBE, user[1:]
        if user and user not in (
            "watch", "channel", "playlist", "results", "feed", "shorts",
            "c", "user", "hashtag",
        ):
            return Platform.YOUTUBE, user

    elif _host_matches(host, "medium.com"):
        if path_parts and path_parts[0].startswith("@"):
            return Platform.MEDIUM, path_parts[0][1:]

    elif _host_matches(host, "substack.com"):
        # https://{user}.substack.com — host'un ilk parcasi kullanici adi
        sub = host.split(".")[0]
        if sub not in ("www", "api", "substack"):
            return Platform.SUBSTACK, sub

    elif _host_matches(host, "deviantart.com"):
        user = first()
        if user and user not in ("watch", "topics", "daily", "studio", "about"):
            return Platform.DEVIANTART, user

    elif _host_matches(host, "behance.net"):
        user = first()
        if user and user not in ("search", "galleries", "hire", "joblist"):
            return Platform.BEHANCE, user

    elif _host_matches(host, "dribbble.com"):
        user = first()
        if user and user not in ("shots", "search", "tags", "jobs", "pros"):
            return Platform.DRIBBBLE, user

    elif _host_matches(host, "pinterest.com"):
        user = first()
        if user and user not in ("pin", "search", "ideas", "settings"):
            return Platform.PINTEREST, user

    elif _host_matches(host, "tumblr.com"):
        blog = host.split(".")[0]
        if blog not in ("www", "tumblr", "new"):
            return Platform.TUMBLR, blog

    elif _host_matches(host, "about.me"):
        user = first()
        if user:
            return Platform.ABOUTME, user

    elif _host_matches(host, "linktr.ee"):
        user = first()
        if user:
            return Platform.LINKTREE, user

    elif _host_matches(host, "myanimelist.net"):
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.MYANIMELIST, path_parts[1]

    elif _host_matches(host, "letterboxd.com"):
        user = first()
        if user and user not in ("films", "members", "search", "settings"):
            return Platform.LETTERBOXD, user

    elif _host_matches(host, "goodreads.com"):
        user = first()
        if user and user not in ("book", "author", "search", "group"):
            return Platform.GOODREADS, user

    elif _host_matches(host, "last.fm"):
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.LASTFM, path_parts[1]

    elif _host_matches(host, "soundcloud.com"):
        user = first()
        if user and user not in ("discover", "stream", "upload", "search", "you"):
            return Platform.SOUNDCLOUD, user

    elif _host_matches(host, "itch.io"):
        sub = host.split(".")[0]
        if sub not in ("www", "itch", "api"):
            return Platform.ITCHIO, sub

    elif _host_matches(host, "t.me"):
        user = first()
        if user and user not in ("s", "share", "iv"):
            return Platform.TELEGRAM, user

    elif _host_matches(host, "vk.com"):
        user = first()
        if user:
            return Platform.VK, user

    elif _host_matches(host, "lichess.org"):
        if len(path_parts) >= 1 and path_parts[0] == "@":
            if len(path_parts) >= 2:
                return Platform.LICHESS, path_parts[1]

    elif _host_matches(host, "wattpad.com"):
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.WATTPAD, path_parts[1]

    elif _host_matches(host, "flickr.com"):
        if len(path_parts) >= 2 and path_parts[0] in ("people", "photos"):
            return Platform.FLICKR, path_parts[1]

    elif _host_matches(host, "musicbrainz.org"):
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.MUSICBRAINZ, path_parts[1]

    elif host == "mastodon.social":
        if path_parts and path_parts[0].startswith("@"):
            return Platform.MASTODON, path_parts[0][1:]

    elif host == "pixelfed.social":
        user = first()
        if user and user not in ("explore", "search", "about"):
            return Platform.PIXELFED, user

    elif _host_matches(host, "rubygems.org"):
        if len(path_parts) >= 2 and path_parts[0] == "profiles":
            return Platform.RUBYGEMS, path_parts[1]

    elif _host_matches(host, "nuget.org"):
        if len(path_parts) >= 2 and path_parts[0] == "profiles":
            return Platform.NUGET, path_parts[1]

    elif _host_matches(host, "packagist.org"):
        if len(path_parts) >= 2 and path_parts[0] == "users":
            return Platform.PACKAGIST, path_parts[1]

    elif _host_matches(host, "9gag.com"):
        if len(path_parts) >= 2 and path_parts[0] == "u":
            return Platform.NINEGAG, path_parts[1]

    elif _host_matches(host, "speakerdeck.com"):
        user = first()
        if user and user not in ("search", "topics", "about"):
            return Platform.SPEAKERDECK, user

    elif _host_matches(host, "marketplace.visualstudio.com"):
        if len(path_parts) >= 2 and path_parts[0] == "publishers":
            return Platform.VSMARKET, path_parts[1]

    elif host == "bsky.app":
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.BLUESKY, path_parts[1]

    elif _host_matches(host, "huggingface.co"):
        user = first()
        if user and user not in ("models", "datasets", "spaces", "docs", "login", "join"):
            return Platform.HUGGINGFACE, user

    elif _host_matches(host, "kitsu.io"):
        if len(path_parts) >= 2 and path_parts[0] == "users":
            return Platform.KITSU, path_parts[1]

    elif _host_matches(host, "wordpress.com"):
        sub = host.split(".")[0]
        if sub not in ("www", "public-api", "wordpress", "en", "blog"):
            return Platform.WORDPRESS, sub

    elif _host_matches(host, "gravatar.com"):
        return Platform.GRAVATAR, None

    return Platform.WEB, None

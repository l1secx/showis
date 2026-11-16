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


def extract_platform_from_url(url: str) -> tuple[Platform, str | None]:
    """URL'den platform ve kullanıcı adı / ID çıkarır."""
    norm = normalize_url(url)
    parsed = urlparse(norm)
    host = parsed.netloc.lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    def first() -> str | None:
        return path_parts[0] if path_parts else None

    if "github.com" in host:
        user = first()
        if user and user not in (
            "features", "pricing", "marketplace", "topics", "search",
            "explore", "about", "orgs", "settings", "login", "signup",
        ):
            return Platform.GITHUB, user

    elif "gitlab.com" in host:
        user = first()
        if user and user not in ("explore", "users", "groups", "help", "api"):
            return Platform.GITLAB, user

    elif "tryhackme.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "p":
            return Platform.TRYHACKME, path_parts[1]

    elif "hackthebox.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.HACKTHEBOX, path_parts[1]

    elif "reddit.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.REDDIT, path_parts[1]

    elif "keybase.io" in host:
        user = first()
        if user and user != "_":
            return Platform.KEYBASE, user

    elif "steamcommunity.com" in host:
        if len(path_parts) >= 2 and path_parts[0] in ("id", "profiles"):
            return Platform.STEAM, path_parts[1]

    elif "dev.to" in host or "developers.com" in host:
        user = first()
        if user and user not in ("api", "enter", "signout", "dashboard"):
            return Platform.DEVTO, user

    elif "hub.docker.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "u":
            return Platform.DOCKERHUB, path_parts[1]

    elif "npmjs.com" in host or "npmjs.org" in host:
        if len(path_parts) >= 1 and path_parts[0] == "~" and len(path_parts) >= 2:
            return Platform.NPM, path_parts[1]

    elif "pypi.org" in host:
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.PYPI, path_parts[1]

    elif "chess.com" in host:
        if len(path_parts) >= 2 and path_parts[0] in ("member", "player"):
            return Platform.CHESS, path_parts[1]

    elif "codewars.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "users":
            return Platform.CODEWARS, path_parts[1]

    elif "hackerrank.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.HACKERRANK, path_parts[1]

    elif "kaggle.com" in host:
        user = first()
        if user and user not in ("competitions", "datasets", "code", "forums", "account"):
            return Platform.KAGGLE, user

    elif "bitbucket.org" in host:
        user = first()
        if user and user not in ("repo", "workspace", "get-started"):
            return Platform.BITBUCKET, user

    elif "codeberg.org" in host:
        user = first()
        if user and user not in ("explore", "assets", "api", "user"):
            return Platform.CODEBERG, user

    elif "codepen.io" in host:
        user = first()
        if user and user not in ("pen", "collections", "trending", "search"):
            return Platform.CODEPEN, user

    elif "hashnode.com" in host or "hashnode.dev" in host:
        if path_parts and path_parts[0].startswith("@"):
            return Platform.HASHNODE, path_parts[0][1:]
        user = first()
        if user and user not in ("feed", "blog", "tags", "search"):
            return Platform.HASHNODE, user

    elif "instagram.com" in host:
        user = first()
        if user and user not in (
            "p", "reel", "reels", "stories", "explore", "accounts",
            "direct", "tv", "developer",
        ):
            return Platform.INSTAGRAM, user

    elif "tiktok.com" in host:
        if path_parts and path_parts[0].startswith("@"):
            return Platform.TIKTOK, path_parts[0][1:]

    elif "youtube.com" in host or "youtu.be" in host:
        user = first()
        if user and user.startswith("@"):
            return Platform.YOUTUBE, user[1:]
        if user and user not in (
            "watch", "channel", "playlist", "results", "feed", "shorts",
            "c", "user", "hashtag",
        ):
            return Platform.YOUTUBE, user

    elif "medium.com" in host:
        if path_parts and path_parts[0].startswith("@"):
            return Platform.MEDIUM, path_parts[0][1:]

    elif host.endswith("substack.com"):
        # https://{user}.substack.com — host'un ilk parcasi kullanici adi
        sub = host.split(".")[0]
        if sub not in ("www", "api", "substack"):
            return Platform.SUBSTACK, sub

    elif "deviantart.com" in host:
        user = first()
        if user and user not in ("watch", "topics", "daily", "studio", "about"):
            return Platform.DEVIANTART, user

    elif "behance.net" in host:
        user = first()
        if user and user not in ("search", "galleries", "hire", "joblist"):
            return Platform.BEHANCE, user

    elif "dribbble.com" in host:
        user = first()
        if user and user not in ("shots", "search", "tags", "jobs", "pros"):
            return Platform.DRIBBBLE, user

    elif "pinterest.com" in host or host.endswith(".pinterest.com"):
        user = first()
        if user and user not in ("pin", "search", "ideas", "settings"):
            return Platform.PINTEREST, user

    elif host.endswith("tumblr.com"):
        blog = host.split(".")[0]
        if blog not in ("www", "tumblr", "new"):
            return Platform.TUMBLR, blog

    elif "about.me" in host:
        user = first()
        if user:
            return Platform.ABOUTME, user

    elif "linktr.ee" in host:
        user = first()
        if user:
            return Platform.LINKTREE, user

    elif "myanimelist.net" in host:
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.MYANIMELIST, path_parts[1]

    elif "letterboxd.com" in host:
        user = first()
        if user and user not in ("films", "members", "search", "settings"):
            return Platform.LETTERBOXD, user

    elif "goodreads.com" in host:
        user = first()
        if user and user not in ("book", "author", "search", "group"):
            return Platform.GOODREADS, user

    elif "last.fm" in host:
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.LASTFM, path_parts[1]

    elif "soundcloud.com" in host:
        user = first()
        if user and user not in ("discover", "stream", "upload", "search", "you"):
            return Platform.SOUNDCLOUD, user

    elif host.endswith("itch.io"):
        sub = host.split(".")[0]
        if sub not in ("www", "itch", "api"):
            return Platform.ITCHIO, sub

    elif "t.me" in host:
        user = first()
        if user and user not in ("s", "share", "iv"):
            return Platform.TELEGRAM, user

    elif "vk.com" in host:
        user = first()
        if user:
            return Platform.VK, user

    elif "lichess.org" in host:
        if len(path_parts) >= 1 and path_parts[0] == "@":
            if len(path_parts) >= 2:
                return Platform.LICHESS, path_parts[1]

    elif "wattpad.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.WATTPAD, path_parts[1]

    elif "flickr.com" in host:
        if len(path_parts) >= 2 and path_parts[0] in ("people", "photos"):
            return Platform.FLICKR, path_parts[1]

    elif "musicbrainz.org" in host:
        if len(path_parts) >= 2 and path_parts[0] == "user":
            return Platform.MUSICBRAINZ, path_parts[1]

    elif host == "mastodon.social":
        if path_parts and path_parts[0].startswith("@"):
            return Platform.MASTODON, path_parts[0][1:]

    elif host == "pixelfed.social":
        user = first()
        if user and user not in ("explore", "search", "about"):
            return Platform.PIXELFED, user

    elif "rubygems.org" in host:
        if len(path_parts) >= 2 and path_parts[0] == "profiles":
            return Platform.RUBYGEMS, path_parts[1]

    elif "nuget.org" in host:
        if len(path_parts) >= 2 and path_parts[0] == "profiles":
            return Platform.NUGET, path_parts[1]

    elif "packagist.org" in host:
        if len(path_parts) >= 2 and path_parts[0] == "users":
            return Platform.PACKAGIST, path_parts[1]

    elif "9gag.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "u":
            return Platform.NINEGAG, path_parts[1]

    elif "speakerdeck.com" in host:
        user = first()
        if user and user not in ("search", "topics", "about"):
            return Platform.SPEAKERDECK, user

    elif "marketplace.visualstudio.com" in host:
        if len(path_parts) >= 2 and path_parts[0] == "publishers":
            return Platform.VSMARKET, path_parts[1]

    elif host == "bsky.app":
        if len(path_parts) >= 2 and path_parts[0] == "profile":
            return Platform.BLUESKY, path_parts[1]

    elif "huggingface.co" in host:
        user = first()
        if user and user not in ("models", "datasets", "spaces", "docs", "login", "join"):
            return Platform.HUGGINGFACE, user

    elif "kitsu.io" in host:
        if len(path_parts) >= 2 and path_parts[0] == "users":
            return Platform.KITSU, path_parts[1]

    elif host.endswith("wordpress.com"):
        sub = host.split(".")[0]
        if sub not in ("www", "public-api", "wordpress", "en", "blog"):
            return Platform.WORDPRESS, sub

    elif "gravatar.com" in host:
        return Platform.GRAVATAR, None

    return Platform.WEB, None

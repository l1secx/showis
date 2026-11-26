"""Hesap istihbaratı: herkese açık profil API'lerinden sahip beyanlı bağlantılar.

Bir hesabın **sahibinin kendisinin** yazdığı alanlar (GitHub `blog`,
`twitter_username`, sosyal hesaplar; GitLab web sitesi/sosyal alanları;
dev.to `github_username`; Keybase kriptografik kanıtları; Gravatar doğrulanmış
hesapları) kimlik zincirinin en güçlü halkalarıdır: bir başkası bu alanları
hedefin adına dolduramaz. Bu modül bu alanları platformdan bağımsız tek bir
``AccountIntel`` biçimine çevirir.

Kanıt türleri (``AccountLink.relation``):

* ``proof``  — kriptografik/doğrulanmış kanıt (Keybase, Gravatar verified).
* ``owner``  — sahibin profil alanına kendisinin yazdığı bağlantı.
* ``readme`` — sahibin kendi profil README'sindeki bağlantı.
* ``bio``    — serbest biyografi metninde geçen bağlantı/kullanıcı adı.

Yalnız anahtarsız, kimlik doğrulaması gerektirmeyen uçlar kullanılır (GitHub
için isteğe bağlı belirteç hız sınırını yükseltir). Ağ erişimi
``showis_engine.http.HttpClient`` üzerinden gider; SSRF kapısı ve HTTP bütçesi
korunur. Hatalar ``AccountIntelError`` ile anlaşılır Türkçe mesaja çevrilir.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from showis_engine.http import BudgetExceededError, HttpClient

MAX_RESPONSE_BYTES = 1_500_000
REQUEST_DEADLINE = 15.0
MAX_LINKS = 40
MAX_EMAILS = 8

GITHUB_API = "https://api.github.com"
GITLAB_API = "https://gitlab.com/api/v4"
KEYBASE_LOOKUP = "https://keybase.io/_/api/1.0/user/lookup.json"
DEVTO_USER = "https://dev.to/api/users/by_username"
BLUESKY_PROFILE = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"
HN_USER = "https://hacker-news.firebaseio.com/v0/user/{h}.json"
GRAVATAR_V3 = "https://api.gravatar.com/v3/profiles/{h}"
GRAVATAR_LEGACY = "https://en.gravatar.com/{h}.json"

_URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]{}|\\^`]+", re.IGNORECASE)
_BARE_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}(?:/[^\s]*)?$", re.IGNORECASE
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HANDLE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MARKDOWN_LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)")

#: Commit e-postalarında sahibine ait olmayan/kişisel olmayan adresler.
_NOREPLY_MARKERS: tuple[str, ...] = (
    "noreply",
    "no-reply",
    "users.noreply.github.com",
    "example.com",
    "localhost",
    "@users.noreply",
    "action@github.com",
)

#: Keybase kanıt türü -> profil URL'si (human_url yoksa).
_KEYBASE_SERVICE_URL: dict[str, str] = {
    "twitter": "https://x.com/{n}",
    "github": "https://github.com/{n}",
    "reddit": "https://www.reddit.com/user/{n}",
    "hackernews": "https://news.ycombinator.com/user?id={n}",
    "mastodon": "",
}

#: GitHub social_accounts sağlayıcısı -> profil URL şablonu (url yoksa).
_GH_PROVIDER_URL: dict[str, str] = {
    "twitter": "https://x.com/{n}",
}


class AccountIntelError(RuntimeError):
    """Tek bir herkese açık uçtaki hata; mesaj kullanıcıya gösterilebilir."""


@dataclass(frozen=True)
class AccountLink:
    url: str
    relation: str
    label: str = ""


@dataclass
class AccountIntel:
    """Platform bağımsız hesap özeti; yalnız sahibin kamuya açtığı alanlar."""

    platform: str
    handle: str
    profile_url: str
    exists: bool = True
    display_name: str = ""
    bio: str = ""
    location: str = ""
    company: str = ""
    avatar_url: str = ""
    created_at: str = ""
    emails: list[tuple[str, str]] = field(default_factory=list)
    links: list[AccountLink] = field(default_factory=list)
    handles: list[tuple[str, str, str]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_link(self, url: object, relation: str, label: str = "") -> None:
        clean = normalize_link(url)
        if not clean or len(self.links) >= MAX_LINKS:
            return
        if any(link.url.rstrip("/") == clean.rstrip("/") for link in self.links):
            return
        self.links.append(AccountLink(url=clean, relation=relation, label=label[:120]))

    def add_email(self, value: object, origin: str) -> None:
        email = str(value or "").strip().lower()
        if not _EMAIL_RE.fullmatch(email) or is_noreply(email):
            return
        if any(existing == email for existing, _ in self.emails):
            return
        if len(self.emails) < MAX_EMAILS:
            self.emails.append((email, origin))

    def add_handle(self, platform: str, handle: object, relation: str) -> None:
        text = str(handle or "").strip().lstrip("@")
        if not text or not _HANDLE_OK.match(text):
            return
        key = (platform, text.lower())
        if any((p, h.lower()) == key for p, h, _ in self.handles):
            return
        self.handles.append((platform, text, relation))

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "handle": self.handle,
            "profile_url": self.profile_url,
            "exists": self.exists,
            "display_name": self.display_name,
            "bio": self.bio,
            "location": self.location,
            "company": self.company,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at,
            "emails": [{"email": e, "origin": o} for e, o in self.emails],
            "links": [
                {"url": link.url, "relation": link.relation, "label": link.label}
                for link in self.links
            ],
            "handles": [
                {"platform": p, "handle": h, "relation": r} for p, h, r in self.handles
            ],
            "sources": list(self.sources),
            "notes": list(self.notes),
        }


def is_noreply(email: str) -> bool:
    lowered = email.lower()
    return any(marker in lowered for marker in _NOREPLY_MARKERS)


def normalize_link(value: object) -> str:
    """Şemasız alan adını https'e tamamlar; geçersizse "" döner."""
    text = html_lib.unescape(str(value or "")).strip().strip("<>").rstrip(".,;)]")
    if not text or len(text) > 2048 or any(ch.isspace() for ch in text):
        return ""
    if text.lower().startswith(("http://", "https://")):
        return text
    if text.startswith("//"):
        return f"https:{text}"
    if _BARE_DOMAIN_RE.match(text):
        return f"https://{text}"
    return ""


def urls_in_text(text: object, *, limit: int = 20) -> list[str]:
    """Serbest metin/markdown içindeki URL'ler (sıra korunur, tekrarsız)."""
    raw = str(text or "")
    found: list[str] = []
    for match in [*_MARKDOWN_LINK_RE.findall(raw), *_URL_RE.findall(raw)]:
        clean = normalize_link(match)
        if clean and clean not in found:
            found.append(clean)
        if len(found) >= limit:
            break
    return found


def _strip_html(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(html_lib.unescape(text).split())


async def _get(
    client: HttpClient,
    url: str,
    *,
    label: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    merged = {"Accept": "application/json"}
    if headers:
        merged.update(headers)
    try:
        response = await asyncio.wait_for(
            client.get(
                url,
                params=params,
                headers=merged,
                check_robots=False,
                follow_redirects=True,
                max_retries=1,
            ),
            timeout=REQUEST_DEADLINE,
        )
    except asyncio.TimeoutError as exc:
        raise AccountIntelError(f"{label}: zaman aşımı") from exc
    except BudgetExceededError as exc:
        raise AccountIntelError(f"{label}: HTTP istek bütçesi tükendi") from exc
    except PermissionError as exc:
        raise AccountIntelError(f"{label}: politika erişimi engelledi") from exc
    except Exception as exc:  # noqa: BLE001 - ağ hatası tek mesaja çevrilir
        raise AccountIntelError(f"{label}: bağlantı kurulamadı ({type(exc).__name__})") from exc
    body = response.content[: MAX_RESPONSE_BYTES + 1]
    if len(body) > MAX_RESPONSE_BYTES:
        raise AccountIntelError(f"{label}: yanıt boyutu sınırı aşıldı")
    return response.status_code, body


async def _get_json(
    client: HttpClient,
    url: str,
    *,
    label: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """JSON GET; 404/410 için None döner, diğer hatalar istisna."""
    status, body = await _get(client, url, label=label, params=params, headers=headers)
    if status in (404, 410):
        return None
    if status == 429 or (status == 403 and b"rate limit" in body.lower()):
        raise AccountIntelError(f"{label}: hız sınırı; sonra tekrar deneyin")
    if status >= 400:
        raise AccountIntelError(f"{label}: HTTP {status} döndürdü")
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise AccountIntelError(f"{label}: geçersiz JSON yanıtı") from exc


def _add_bio(intel: AccountIntel, text: str) -> None:
    for url in urls_in_text(text, limit=10):
        intel.add_link(url, "bio")
    for email in _EMAIL_RE.findall(text or ""):
        intel.add_email(email, "bio")


# ---------------------------------------------------------------- GitHub


def _github_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_github(
    client: HttpClient,
    handle: str,
    *,
    token: str | None = None,
    include_events: bool = True,
) -> AccountIntel | None:
    """GitHub kullanıcı profili, sosyal hesaplar, profil README ve commit e-postaları."""
    login = handle.strip().lstrip("@")
    if not _HANDLE_OK.match(login):
        return None
    headers = _github_headers(token)
    user_url = f"{GITHUB_API}/users/{quote(login)}"
    user = await _get_json(client, user_url, label="GitHub", headers=headers)
    if not isinstance(user, dict) or not user.get("login"):
        return None
    login = str(user["login"])
    intel = AccountIntel(
        platform="github",
        handle=login,
        profile_url=str(user.get("html_url") or f"https://github.com/{login}"),
        display_name=str(user.get("name") or "")[:200],
        bio=str(user.get("bio") or "")[:1000],
        location=str(user.get("location") or "")[:200],
        company=str(user.get("company") or "")[:200],
        avatar_url=str(user.get("avatar_url") or ""),
        created_at=str(user.get("created_at") or ""),
        sources=[f"https://api.github.com/users/{login}"],
    )
    if user.get("blog"):
        intel.add_link(user["blog"], "owner", "GitHub web sitesi alanı")
    if user.get("twitter_username"):
        intel.add_link(f"https://x.com/{user['twitter_username']}", "owner", "GitHub X alanı")
    if user.get("email"):
        intel.add_email(user["email"], "github-profile")
    company = intel.company.strip()
    if company.startswith("@") and _HANDLE_OK.match(company[1:]):
        intel.add_link(f"https://github.com/{company[1:]}", "owner", "GitHub şirket alanı")
    _add_bio(intel, intel.bio)

    try:
        socials = await _get_json(
            client, f"{user_url}/social_accounts", label="GitHub sosyal", headers=headers
        )
    except AccountIntelError as exc:
        intel.notes.append(str(exc))
        socials = None
    if isinstance(socials, list):
        for row in socials[:20]:
            if not isinstance(row, dict):
                continue
            provider = str(row.get("provider") or "generic")
            url = str(row.get("url") or "")
            if not url and provider in _GH_PROVIDER_URL:
                continue
            intel.add_link(url, "owner", f"GitHub sosyal hesap ({provider})")
        intel.sources.append(f"https://api.github.com/users/{login}/social_accounts")

    # Profil README: yalnız sahibi `login/login` deposuna yazabilir.
    readme_url = f"https://raw.githubusercontent.com/{login}/{login}/HEAD/README.md"
    try:
        status, body = await _get(client, readme_url, label="GitHub README", headers={})
    except AccountIntelError as exc:
        intel.notes.append(str(exc))
        status, body = 0, b""
    if status == 200 and body:
        text = body.decode("utf-8", errors="replace")[:200_000]
        for url in urls_in_text(text, limit=40):
            if _is_badge_or_asset(url):
                continue
            intel.add_link(url, "readme", "GitHub profil README")
        for email in _EMAIL_RE.findall(text):
            intel.add_email(email, "github-readme")
        intel.sources.append(f"https://github.com/{login}/{login}")

    if include_events:
        try:
            events = await _get_json(
                client,
                f"{user_url}/events/public",
                label="GitHub olaylar",
                params={"per_page": "100"},
                headers=headers,
            )
        except AccountIntelError as exc:
            intel.notes.append(str(exc))
            events = None
        for email, name in _commit_emails(events, login=login, display=intel.display_name):
            intel.add_email(email, "github-commit")
            if name:
                intel.notes.append(f"commit yazar adı: {name[:80]}")
    return intel


def _is_badge_or_asset(url: str) -> bool:
    lowered = url.lower()
    if any(
        host in lowered
        for host in (
            "shields.io",
            "readme-typing-svg",
            "github-readme-stats",
            "komarev.com",
            "githubusercontent.com",
            "vercel.app/api",
            "skillicons.dev",
            "devicon",
            "streak-stats",
            "github-profile-trophy",
            "activity-graph",
            "giphy.com",
            "camo.",
        )
    ):
        return True
    path = lowered.split("?", 1)[0]
    return path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js"))


def _commit_emails(events: Any, *, login: str, display: str) -> list[tuple[str, str]]:
    """Kullanıcının kendi push olaylarındaki commit yazar e-postaları.

    Başkasının commit'i (birleştirme, eş yazar) yanlış pozitif üretir; bu
    yüzden yazar adı GitHub adı/kullanıcı adıyla eşleşen ya da en az iki kez
    tekrarlanan e-postalar alınır.
    """
    if not isinstance(events, list):
        return []
    names = {login.casefold(), display.casefold()} - {""}
    counts: dict[str, int] = {}
    matched: dict[str, str] = {}
    for event in events[:100]:
        if not isinstance(event, dict) or event.get("type") != "PushEvent":
            continue
        payload = event.get("payload") or {}
        for commit in (payload.get("commits") or [])[:30]:
            author = (commit or {}).get("author") or {}
            email = str(author.get("email") or "").strip().lower()
            name = str(author.get("name") or "").strip()
            if not email or is_noreply(email) or not _EMAIL_RE.fullmatch(email):
                continue
            counts[email] = counts.get(email, 0) + 1
            if name.casefold() in names:
                matched[email] = name
    result: list[tuple[str, str]] = []
    for email, count in sorted(counts.items(), key=lambda item: -item[1]):
        if email in matched or count >= 2:
            result.append((email, matched.get(email, "")))
    return result[:4]


# ---------------------------------------------------------------- GitLab


async def fetch_gitlab(client: HttpClient, handle: str) -> AccountIntel | None:
    login = handle.strip().lstrip("@")
    if not _HANDLE_OK.match(login):
        return None
    rows = await _get_json(
        client, f"{GITLAB_API}/users", label="GitLab", params={"username": login}
    )
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    user_id = rows[0].get("id")
    detail = await _get_json(client, f"{GITLAB_API}/users/{user_id}", label="GitLab")
    data = detail if isinstance(detail, dict) else rows[0]
    username = str(data.get("username") or login)
    intel = AccountIntel(
        platform="gitlab",
        handle=username,
        profile_url=str(data.get("web_url") or f"https://gitlab.com/{username}"),
        display_name=str(data.get("name") or "")[:200],
        bio=str(data.get("bio") or "")[:1000],
        location=str(data.get("location") or "")[:200],
        company=str(data.get("organization") or "")[:200],
        avatar_url=str(data.get("avatar_url") or ""),
        created_at=str(data.get("created_at") or ""),
        sources=[f"{GITLAB_API}/users/{user_id}"],
    )
    if data.get("website_url"):
        intel.add_link(data["website_url"], "owner", "GitLab web sitesi alanı")
    if data.get("twitter"):
        intel.add_link(f"https://x.com/{data['twitter']}", "owner", "GitLab X alanı")
    if data.get("linkedin"):
        value = str(data["linkedin"])
        url = value if value.startswith("http") else f"https://www.linkedin.com/in/{value}"
        intel.add_link(url, "owner", "GitLab LinkedIn alanı")
    if data.get("github"):
        intel.add_link(f"https://github.com/{data['github']}", "owner", "GitLab GitHub alanı")
    if data.get("discord"):
        intel.add_handle("discord", data["discord"], "owner")
    if data.get("public_email"):
        intel.add_email(data["public_email"], "gitlab-profile")
    _add_bio(intel, intel.bio)
    return intel


# ---------------------------------------------------------------- Keybase


async def fetch_keybase(client: HttpClient, handle: str) -> AccountIntel | None:
    login = handle.strip().lstrip("@")
    if not _HANDLE_OK.match(login):
        return None
    payload = await _get_json(
        client,
        KEYBASE_LOOKUP,
        label="Keybase",
        params={"usernames": login, "fields": "basics,profile,proofs_summary"},
    )
    them = (payload or {}).get("them") if isinstance(payload, dict) else None
    if not isinstance(them, list) or not them or not isinstance(them[0], dict):
        return None
    row = them[0]
    basics = row.get("basics") or {}
    profile = row.get("profile") or {}
    username = str(basics.get("username") or login)
    intel = AccountIntel(
        platform="keybase",
        handle=username,
        profile_url=f"https://keybase.io/{username}",
        display_name=str(profile.get("full_name") or "")[:200],
        bio=str(profile.get("bio") or "")[:1000],
        location=str(profile.get("location") or "")[:200],
        sources=[f"https://keybase.io/{username}"],
    )
    proofs = ((row.get("proofs_summary") or {}).get("all")) or []
    for proof in proofs[:30]:
        if not isinstance(proof, dict):
            continue
        kind = str(proof.get("proof_type") or "")
        name = str(proof.get("nametag") or "")
        url = str(proof.get("service_url") or "")
        if not url:
            template = _KEYBASE_SERVICE_URL.get(kind, "")
            url = template.format(n=name) if template and name else ""
        if kind in {"generic_web_site", "dns"} and name and not url:
            url = f"https://{name}"
        intel.add_link(url, "proof", f"Keybase kanıtı ({kind})")
    _add_bio(intel, intel.bio)
    return intel


# ---------------------------------------------------------------- dev.to


async def fetch_devto(client: HttpClient, handle: str) -> AccountIntel | None:
    login = handle.strip().lstrip("@")
    if not _HANDLE_OK.match(login):
        return None
    data = await _get_json(client, DEVTO_USER, label="dev.to", params={"url": login})
    if not isinstance(data, dict) or not data.get("username"):
        return None
    username = str(data["username"])
    intel = AccountIntel(
        platform="devto",
        handle=username,
        profile_url=f"https://dev.to/{username}",
        display_name=str(data.get("name") or "")[:200],
        bio=str(data.get("summary") or "")[:1000],
        location=str(data.get("location") or "")[:200],
        avatar_url=str(data.get("profile_image") or ""),
        created_at=str(data.get("joined_at") or ""),
        sources=[f"https://dev.to/{username}"],
    )
    if data.get("github_username"):
        intel.add_link(f"https://github.com/{data['github_username']}", "owner", "dev.to GitHub")
    if data.get("twitter_username"):
        intel.add_link(f"https://x.com/{data['twitter_username']}", "owner", "dev.to X")
    if data.get("website_url"):
        intel.add_link(data["website_url"], "owner", "dev.to web sitesi")
    _add_bio(intel, intel.bio)
    return intel


# ---------------------------------------------------------------- Bluesky


async def fetch_bluesky(client: HttpClient, handle: str) -> AccountIntel | None:
    actor = handle.strip().lstrip("@").lower()
    if not actor:
        return None
    if "." not in actor:
        actor = f"{actor}.bsky.social"
    data = await _get_json(client, BLUESKY_PROFILE, label="Bluesky", params={"actor": actor})
    if not isinstance(data, dict) or not data.get("handle"):
        return None
    name = str(data["handle"])
    intel = AccountIntel(
        platform="bluesky",
        handle=name,
        profile_url=f"https://bsky.app/profile/{name}",
        display_name=str(data.get("displayName") or "")[:200],
        bio=str(data.get("description") or "")[:1000],
        avatar_url=str(data.get("avatar") or ""),
        created_at=str(data.get("createdAt") or ""),
        sources=[f"https://bsky.app/profile/{name}"],
    )
    # Özel alan adı handle'ı (ör. ali.dev) DNS/HTTP ile doğrulanmıştır.
    if not name.endswith(".bsky.social"):
        intel.add_link(f"https://{name}", "proof", "Bluesky alan adı doğrulaması")
    _add_bio(intel, intel.bio)
    return intel


# ---------------------------------------------------------------- Hacker News


async def fetch_hackernews(client: HttpClient, handle: str) -> AccountIntel | None:
    login = handle.strip()
    if not _HANDLE_OK.match(login):
        return None
    data = await _get_json(client, HN_USER.format(h=quote(login)), label="Hacker News")
    if not isinstance(data, dict) or not data.get("id"):
        return None
    about = _strip_html(data.get("about"))
    intel = AccountIntel(
        platform="hackernews",
        handle=str(data["id"]),
        profile_url=f"https://news.ycombinator.com/user?id={data['id']}",
        bio=about[:1000],
        sources=[f"https://news.ycombinator.com/user?id={data['id']}"],
    )
    for url in urls_in_text(str(data.get("about") or "").replace("&#x2F;", "/"), limit=10):
        intel.add_link(url, "bio", "HN hakkında")
    for email in _EMAIL_RE.findall(about):
        intel.add_email(email, "bio")
    return intel


# ---------------------------------------------------------------- Gravatar


async def fetch_gravatar(client: HttpClient, email: str) -> AccountIntel | None:
    """E-posta özetinden Gravatar profili; doğrulanmış hesaplar `proof` sayılır."""
    address = email.strip().lower()
    if not _EMAIL_RE.fullmatch(address):
        return None
    sha = hashlib.sha256(address.encode()).hexdigest()
    md5 = hashlib.md5(address.encode(), usedforsecurity=False).hexdigest()
    data = await _get_json(client, GRAVATAR_V3.format(h=sha), label="Gravatar")
    if isinstance(data, dict) and (data.get("profile_url") or data.get("display_name")):
        handle = str(data.get("profile_url") or "").rstrip("/").rsplit("/", 1)[-1] or sha[:12]
        intel = AccountIntel(
            platform="gravatar",
            handle=handle,
            profile_url=str(data.get("profile_url") or f"https://gravatar.com/{sha}"),
            display_name=str(data.get("display_name") or "")[:200],
            bio=str(data.get("description") or "")[:1000],
            location=str(data.get("location") or "")[:200],
            company=str(data.get("company") or "")[:200],
            avatar_url=str(data.get("avatar_url") or ""),
            sources=[GRAVATAR_V3.format(h=sha)],
        )
        for account in (data.get("verified_accounts") or [])[:20]:
            if isinstance(account, dict):
                service = account.get("service_label") or account.get("service_type") or ""
                intel.add_link(account.get("url"), "proof", f"Gravatar doğrulanmış ({service})")
        for link in (data.get("links") or [])[:20]:
            if isinstance(link, dict):
                intel.add_link(link.get("url"), "owner", "Gravatar bağlantısı")
        _add_bio(intel, intel.bio)
        return intel

    legacy = await _get_json(client, GRAVATAR_LEGACY.format(h=md5), label="Gravatar")
    entries = (legacy or {}).get("entry") if isinstance(legacy, dict) else None
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    entry = entries[0]
    intel = AccountIntel(
        platform="gravatar",
        handle=str(entry.get("preferredUsername") or md5[:12]),
        profile_url=str(entry.get("profileUrl") or f"https://gravatar.com/{md5}"),
        display_name=str(entry.get("displayName") or "")[:200],
        bio=str(entry.get("aboutMe") or "")[:1000],
        location=str(entry.get("currentLocation") or "")[:200],
        avatar_url=str(entry.get("thumbnailUrl") or ""),
        sources=[GRAVATAR_LEGACY.format(h=md5)],
    )
    for account in (entry.get("accounts") or [])[:20]:
        if isinstance(account, dict):
            verified = str(account.get("verified") or "").lower() == "true"
            intel.add_link(
                account.get("url"),
                "proof" if verified else "owner",
                f"Gravatar hesabı ({account.get('shortname') or ''})",
            )
    for link in (entry.get("urls") or [])[:20]:
        if isinstance(link, dict):
            intel.add_link(link.get("value"), "owner", "Gravatar bağlantısı")
    _add_bio(intel, intel.bio)
    return intel


#: Platform adı -> kullanıcı adı alan getirici. Gravatar e-posta alır (ayrı).
HANDLE_FETCHERS = {
    "github": fetch_github,
    "gitlab": fetch_gitlab,
    "keybase": fetch_keybase,
    "devto": fetch_devto,
    "bluesky": fetch_bluesky,
    "hackernews": fetch_hackernews,
}

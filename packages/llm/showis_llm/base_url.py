"""LLM/uygulama dış çağrıları için base_url politikası (SEC-02, SEC-03).

Neden burada: `showis_llm` paketi `showis_engine`'e bağımlı değil (paketleme
değişmesin). Bu modül **şema + host allowlist** kontrolünü yapar; DNS/private-IP
(SSRF) kontrolü çağıran katmanda `showis_engine.policy.guard_url` ile yapılır.
İstemci de her çağrıdan önce bu allowlist'i yeniden doğrular (defense in depth):
DB'de eski/elle yazılmış bir `base_url` kalmış olsa bile sır allowlist dışı bir
host'a gitmez.

Kurallar:

- Yalnız `https`. Tek istisna: `localhost`/`127.0.0.1`/`::1` ve
  `SHOWIS_ALLOW_INSECURE_BASE_URL=1` (yerel geliştirme).
- Host allowlist: bilinen LLM sağlayıcıları + `*.opencode.ai`;
  `SHOWIS_LLM_BASE_URL_ALLOWLIST` (virgülle ayrılmış; `*.ornek.com` destekli).
- URL'de kullanıcı bilgisi (`user:pass@`), query veya fragment olamaz.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

ALLOWLIST_ENV = "SHOWIS_LLM_BASE_URL_ALLOWLIST"
INSECURE_ENV = "SHOWIS_ALLOW_INSECURE_BASE_URL"

#: Varsayılan izinli host'lar (yalnız https).
DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "api.openai.com",
        "opencode.ai",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "api.groq.com",
        "api.mistral.ai",
        "openrouter.ai",
    }
)

#: Ek olarak izinli alan adı sonekleri.
DEFAULT_ALLOWED_SUFFIXES: tuple[str, ...] = (".opencode.ai",)

#: https istisnası yalnız bu host'lar için (dev kolaylığı).
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})

MAX_URL_LENGTH = 512


class BaseUrlError(ValueError):
    """base_url politikası ihlali (API'de 422'ye eşlenir)."""


def _split_csv(raw: str) -> list[str]:
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def allowed_hosts() -> frozenset[str]:
    """Varsayılan + env allowlist (host'lar)."""
    extra = _split_csv(os.getenv(ALLOWLIST_ENV, ""))
    hosts = set(DEFAULT_ALLOWED_HOSTS)
    for item in extra:
        if not item.startswith("*."):
            hosts.add(item)
    return frozenset(hosts)


def allowed_suffixes() -> tuple[str, ...]:
    """Varsayılan + env allowlist (alan adı sonekleri)."""
    extra = [item[1:] for item in _split_csv(os.getenv(ALLOWLIST_ENV, "")) if item.startswith("*.")]
    return DEFAULT_ALLOWED_SUFFIXES + tuple(extra)


def insecure_allowed() -> bool:
    return os.getenv(INSECURE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def host_allowed(host: str) -> bool:
    host = (host or "").lower()
    if not host:
        return False
    if host in allowed_hosts():
        return True
    return any(host == suffix[1:] or host.endswith(suffix) for suffix in allowed_suffixes())


def validate_base_url(raw: object, *, allow_insecure: bool | None = None) -> str:
    """Doğrulanmış, normalize edilmiş base_url döndürür; aksi halde hata fırlatır."""
    if not isinstance(raw, str) or not raw.strip():
        raise BaseUrlError("base_url zorunlu ve metin olmali")
    candidate = raw.strip()
    if len(candidate) > MAX_URL_LENGTH:
        raise BaseUrlError(f"base_url cok uzun (>{MAX_URL_LENGTH})")
    if any(ch.isspace() for ch in candidate):
        raise BaseUrlError("base_url bosluk iceremez")

    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise BaseUrlError("base_url http(s) semasi ile baslamali")
    if parsed.username or parsed.password:
        raise BaseUrlError("base_url kullanici bilgisi iceremez")
    if parsed.query or parsed.fragment:
        raise BaseUrlError("base_url query/fragment iceremez")

    host = (parsed.hostname or "").lower()
    if not host:
        raise BaseUrlError("base_url host icermeli")

    insecure_ok = insecure_allowed() if allow_insecure is None else allow_insecure
    if scheme == "http":
        if not (host in LOOPBACK_HOSTS and insecure_ok):
            raise BaseUrlError(
                "base_url https olmali (yalniz localhost icin "
                f"http, {INSECURE_ENV}=1 ile)"
            )

    if not host_allowed(host) and not (host in LOOPBACK_HOSTS and insecure_ok):
        allowed = ", ".join(sorted(allowed_hosts()))
        raise BaseUrlError(f"base_url host izinli degil: {host} (izinliler: {allowed})")

    normalized = candidate.rstrip("/")
    return normalized


def is_allowed_plain_http(raw: object) -> bool:
    """Yalnız dev istisnası olarak http loopback mi?"""
    try:
        url = validate_base_url(raw)
    except BaseUrlError:
        return False
    return urlparse(url).scheme.lower() == "http"

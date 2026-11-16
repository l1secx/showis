"""Log redaksiyonu — sirlarin (API anahtarlari, token'lar) log'a sizmasini onler.

SEC-04/SEC-08/SEC-11 ve T-332 kapsami:
* `httpx` INFO seviyesinde **tam URL** logluyor (`...&api_key=<GERCEK>`); bu yuzden
  `httpx`/`httpcore` logger'lari varsayilan olarak `WARNING`'e cekilir (URL loglanmaz).
* Filtre artik `record.getMessage()` uzerinde calisir ve redakte ederse
  `record.args = ()` yapar — SEC-11'deki "msg + args ayri redakte ediliyor, sirlar
  parcali kaciyor" acigi kapanir.
* URL/query parametreleri (`api_key`, `key`, `token`, `access_token`, `cx`, ...),
  bilinen anahtar onekleri (`sk-`, `ghp_`, `AIza`, `xai-`, `github_pat_`) ve
  baglamdan bagimsiz uzun hex token'lar (32-64) maskelenir.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Maskelerken kullanilan isaret (testler ve log okunurlugu icin sabit)
MASK = "****"

# GitHub PAT, OpenAI/LLM, Brave, SerpAPI, generic bearer token kaliplari
_SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xai-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)([\"'\s:=]+)([^\s\"',}]{6,})"),
]

#: URL query parametreleri: degeri ne olursa olsun maskelenir
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "key",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "client_secret",
        "secret",
        "password",
        "cx",
        "subscription-key",
        "subscription_key",
        "auth",
    }
)

#: `?api_key=...` / `&token=...` gibi gomulu query parcalari (metin icinde)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|key|token|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|secret|password|cx|subscription[-_]key|auth)=)([^&\s\"'<>]+)"
)

#: Baglamdan bagimsiz uzun hex token (orn. Google CSE anahtari `4a0d...`)
_HEX_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{32,64}(?![0-9A-Za-z])")
#: Hex'in "guvenli" baglamlari: kanit hash'leri maskelenmesin
_SAFE_HASH_CONTEXT_RE = re.compile(
    r"(?i)(content_hash|sha-?256|digest|hash)\s*[:=]\s*$"
)

# PII (KVKK): e-posta adresleri loglarda gorunmez
_PII_PATTERNS = [re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")]

#: URL loglamasini kapatan logger'lar (T-332): INFO'da tam URL yaziyorlardi
_QUIET_LOGGERS = ("httpx", "httpcore", "httpcore.http11", "httpcore.connection")
_QUIET_LEVEL = logging.WARNING


def _mask_hex(match: re.Match[str]) -> str:
    """Uzun hex dizisini maskeler; kanit hash'i baglamindaysa korur."""
    context = match.string[max(0, match.start() - 24) : match.start()]
    if _SAFE_HASH_CONTEXT_RE.search(context):
        return match.group(0)
    return MASK


def redact_secrets(text: str) -> str:
    """Metindeki gizli anahtar, token ve e-posta kalintilarini maskeler."""
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            redacted = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", redacted)
        else:
            redacted = pattern.sub(MASK, redacted)
    redacted = _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{MASK}", redacted)
    redacted = _HEX_TOKEN_RE.sub(_mask_hex, redacted)
    for pii in _PII_PATTERNS:
        redacted = pii.sub(MASK, redacted)
    return redacted


def redact_url(url: str) -> str:
    """URL'yi loglamak icin guvenli hale getirir (hassas query parametreleri maskeli).

    Yol ve host korunur (teshis icin gerekli), `?api_key=...` gibi degerler
    `****` olur. URL degilse/parse edilemezse metin redaksiyonuna duser.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:  # pragma: no cover - bozuk URL
        return redact_secrets(url)
    if not parts.scheme and not parts.netloc:
        return redact_secrets(url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    safe_pairs = [
        (key, MASK if key.lower() in _SENSITIVE_QUERY_KEYS else value)
        for key, value in query_pairs
    ]
    query = urlencode(safe_pairs, doseq=True).replace("%2A%2A%2A%2A", MASK)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


_record_factory_installed = False


def _redacting_log_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    """Her log kaydini olusum aninda redakte eder.

    Neden factory? `logger.addFilter` yalniz o logger'a yazilan kayitlari,
    `handler.addFilter` yalniz o handler'i kapsar. Celery/uvicorn gibi sonradan
    handler ekleyen bilesenler (worker loglari) sirlari yine basilabilirdi;
    factory **tum** kayitlari kapsar.
    """
    record = _original_record_factory(*args, **kwargs)  # type: ignore[operator]
    try:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
    except Exception:  # pragma: no cover - log yolu asla patlamamali
        pass
    return record


class SecretRedactionFilter(logging.Filter):
    """Tum log kayitlarindan sir kalintilarini temizler (msg + args birlikte)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = redact_secrets(message)
            if redacted != message:
                # SEC-11: mesaj birlestirilerek redakte edilir; args temizlenir ki
                # handler tekrar sirlari birlestirmesin.
                record.msg = redacted
                record.args = ()
        except Exception:  # pragma: no cover - log filtresi asla exception uretmemeli
            pass
        return True


_original_record_factory = logging.getLogRecordFactory()


def install_redaction_filter() -> None:
    """Root logger'a redaksiyon filtresini kurar (idempotent).

    Ayrica `httpx`/`httpcore` logger'larini `WARNING`'e cekerek INFO seviyesindeki
    tam URL loglamasini kapatir (T-332: SerpAPI `api_key` sizintisi).
    """
    filt = SecretRedactionFilter()
    root = logging.getLogger()
    if not any(isinstance(f, SecretRedactionFilter) for f in root.filters):
        root.addFilter(filt)
    for handler in root.handlers:
        if not any(isinstance(f, SecretRedactionFilter) for f in handler.filters):
            handler.addFilter(filt)
    global _record_factory_installed
    if not _record_factory_installed:
        logging.setLogRecordFactory(_redacting_log_record_factory)
        _record_factory_installed = True

    for name in _QUIET_LOGGERS:
        logger = logging.getLogger(name)
        if logger.level == logging.NOTSET or logger.level < _QUIET_LEVEL:
            logger.setLevel(_QUIET_LEVEL)

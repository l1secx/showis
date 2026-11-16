"""Log redaksiyonu — sirlarin (API anahtarlari, token'lar) log'a sizmasini onler (SEC4)."""

from __future__ import annotations

import logging
import re

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


# PII (KVKK): e-posta adresleri loglarda gorunmez
_PII_PATTERNS = [re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")]


def redact_secrets(text: str) -> str:
    """Metindeki gizli anahtar ve e-posta kalintilarini maskeler."""
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            redacted = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}****", redacted)
        else:
            redacted = pattern.sub("****", redacted)
    for pii in _PII_PATTERNS:
        redacted = pii.sub("****", redacted)
    return redacted


class SecretRedactionFilter(logging.Filter):
    """Tum log kayitlarindan sir kalintilarini temizler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_secrets(str(record.msg))
            if record.args:
                record.args = tuple(
                    redact_secrets(str(a)) if isinstance(a, str) else a for a in record.args
                )
        except Exception:  # pragma: no cover - log filtresi asla exception uretmemeli
            pass
        return True


def install_redaction_filter() -> None:
    """Root logger'a redaksiyon filtresini kurar (idempotent)."""
    filt = SecretRedactionFilter()
    root = logging.getLogger()
    if not any(isinstance(f, SecretRedactionFilter) for f in root.filters):
        root.addFilter(filt)
    for handler in root.handlers:
        if not any(isinstance(f, SecretRedactionFilter) for f in handler.filters):
            handler.addFilter(filt)

"""Hata metinlerinden sır/URL temizliği (SEC-08).

Saldırganın veya meraklı bir operatörün görebildiği hata metinlerinde
(`settings /test` yanıtı, `scans.error`, `tasks.error`, audit `details`)
sağlayıcı anahtarı veya URL query'si bulunmamalı.

`core/logging.py` log filtreleri için ayrı bir katman; burası **veri**
temizliği yapar: DB'ye ve API yanıtına yazılan metinler buradan geçmeli.
"""

from __future__ import annotations

import re

#: URL query'sindeki sır taşıyan parametreler (SerpAPI `api_key=`, CSE `key=` …)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|access_token|refresh_token|secret|password|cx|sig|signature|code)=)"
    r"[^&\s'\"<>]+"
)

#: Authorization başlığı kalıntısı
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")

#: Bilinen token biçimleri
_TOKEN_SHAPES = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"xai-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
]

#: `api_key: xxx` / `token=xxx` biçimleri
_KEY_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password)([\"'\s:=]+)([^\s\"',}&<>]{6,})"
)

#: URL içindeki kullanıcı bilgisi (user:pass@host)
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")


def redact_secret_text(text: str) -> str:
    """Metindeki anahtar/parola kalıntılarını ve e-postaları maskeler."""
    if not text:
        return text
    redacted = _URL_USERINFO.sub(r"\1***@", str(text))
    redacted = _SECRET_QUERY.sub(r"\1****", redacted)
    redacted = _BEARER.sub("Bearer ****", redacted)
    for pattern in _TOKEN_SHAPES:
        redacted = pattern.sub("****", redacted)
    redacted = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}****", redacted)
    return redacted


def sanitize_error_text(text: str, *, max_length: int = 400) -> str:
    """Tek satır, sırsız, uzunluğu sınırlı hata metni."""
    if not text:
        return ""
    cleaned = redact_secret_text(str(text))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1] + "…"
    return cleaned


def sanitize_error(exc: BaseException, *, max_length: int = 400) -> str:
    """İstisnayı ``Tip: sırsız mesaj`` biçimine çevirir.

    httpx hatalarında URL query'si ve sağlayıcı gövdesi temizlenir; yalnız
    durum kodu ve istisna tipi anlamlı kalır.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    name = type(exc).__name__
    message = sanitize_error_text(str(exc), max_length=max_length)
    if status_code is not None:
        return f"{name} (HTTP {status_code}): {message}"
    return f"{name}: {message}"


def provider_error_message(provider: str, status_code: int | None) -> str:
    """İstemciye dönen sabit kategori metni (sağlayıcı gövdesi asla eklenmez)."""
    if status_code == 401 or status_code == 403:
        return f"{provider} kimlik doğrulaması başarısız (HTTP {status_code})"
    if status_code == 429:
        return f"{provider} hız/kota sınırı (HTTP 429)"
    if status_code is not None and status_code >= 500:
        return f"{provider} geçici olarak kullanılamıyor (HTTP {status_code})"
    if status_code is None:
        return f"{provider} bağlantısı kurulamadı"
    return f"{provider} beklenmeyen yanıt (HTTP {status_code})"

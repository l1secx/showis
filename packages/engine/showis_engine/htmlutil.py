"""HTML yardımcıları — bağımlılıksız, regex tabanlı minimal çıkarım."""

from __future__ import annotations

import html
import re

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"\s+")


def strip_html(raw: str) -> str:
    """HTML'i düz metne çevirir (script/style kaldırılır, entity decode edilir)."""
    if not raw:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def meta_content(raw_html: str, *, prop: str | None = None, name: str | None = None) -> str | None:
    """<meta property|name="..." content="..."> değerini döndürür."""
    if prop:
        pattern = re.compile(
            r'<meta[^>]+property=["\']'
            + re.escape(prop)
            + r'["\'][^>]+content=["\']([^"\']+)["\']',
            re.IGNORECASE,
        )
        m = pattern.search(raw_html)
        if m:
            return html.unescape(m.group(1)).strip()
        # attribute order reversed
        pattern2 = re.compile(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']'
            + re.escape(prop)
            + r'["\']',
            re.IGNORECASE,
        )
        m2 = pattern2.search(raw_html)
        return html.unescape(m2.group(1)).strip() if m2 else None
    return None


def og_image(raw_html: str) -> str | None:
    return meta_content(raw_html, prop="og:image")


def og_title(raw_html: str) -> str | None:
    return meta_content(raw_html, prop="og:title")

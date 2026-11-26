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


def _meta_patterns(attribute: str, value: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """`<meta attr="value" content="...">` için iki attribute sırası."""
    forward = re.compile(
        rf"<meta[^>]+{attribute}=[\"']{re.escape(value)}[\"'][^>]+content=[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )
    reversed_ = re.compile(
        rf"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+{attribute}=[\"']{re.escape(value)}[\"']",
        re.IGNORECASE,
    )
    return forward, reversed_


def meta_content(
    raw_html: str,
    *,
    prop: str | None = None,
    name: str | None = None,
) -> str | None:
    """`<meta property|name="..." content="...">` değerini döndürür.

    `prop` verilirse `property=`, `name` verilirse `name=` aranır. İkisi birlikte
    verilirse önce `prop`, bulunamazsa `name` denenir (ör. og:description →
    description geri dönüşü).
    """
    if not raw_html:
        return None
    for attribute, value in (("property", prop), ("name", name)):
        if not value:
            continue
        for pattern in _meta_patterns(attribute, value):
            match = pattern.search(raw_html)
            if match:
                return html.unescape(match.group(1)).strip()
    return None


def og_image(raw_html: str) -> str | None:
    return meta_content(raw_html, prop="og:image")


def og_title(raw_html: str) -> str | None:
    return meta_content(raw_html, prop="og:title")

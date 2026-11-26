"""WhatsMyName veri setinden türetilmiş site tanımları.

Kaynak: https://github.com/WebBreacher/WhatsMyName (`wmn-data.json`)
Lisans: CC BY-SA 4.0 — ayrıntı: `WMN_NOTICE.md`.

WMN alan eşlemesi:
* ``e_code`` / ``e_string`` → hesap VAR (present)
* ``m_code`` / ``m_string`` → hesap YOK (absent)

Yalnız anahtarsız, iki işaretli, korumasız ve geçerli kayıtlar alınır.
Tüm WMN siteleri ``Platform.WEB`` altında çalışır; entity çakışmasını önlemek
için her spec benzersiz ``id_namespace`` taşır. Mod bazlı kapsam seçimi
``sites.select_sites`` içindedir.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from showis_contracts import Platform

from .sites import SiteSpec

_DATA_PATH = Path(__file__).with_name("wmn-data.json")
_ALLOWED_CATEGORIES = {
    "art",
    "blog",
    "business",
    "coding",
    "finance",
    "gaming",
    "health",
    "hobby",
    "images",
    "misc",
    "music",
    "news",
    "political",
    "shopping",
    "social",
    "tech",
    "video",
}
_TIER2_CATEGORIES = {"coding", "gaming", "social", "tech", "blog", "finance", "business"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    slug = _SLUG_RE.sub("_", value.lower()).strip("_")
    return slug[:40] or "site"


def _host(url_template: str) -> str:
    match = re.match(r"https?://([^/]+)", url_template.replace("{u}", "x"), re.I)
    return (match.group(1).lower().removeprefix("www.") if match else "")


def load_wmn_sites(
    existing_specs: Iterable[SiteSpec],
    extra_hosts: Iterable[str] = (),
) -> list[SiteSpec]:
    """WMN JSON'undan SiteSpec listesi üretir; mevcut hostları atlar.

    ``extra_hosts``: publicapi/ derin connector'ların kapsadığı host'lar. Bu
    host'lar WMN'de tekrar tanımlıysa (ör. bluesky, huggingface, wordpress)
    atlanır; aksi halde aynı kontrol iki connector'da çift istek üretir.
    """
    if not _DATA_PATH.is_file():
        return []
    try:
        payload = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    existing_hosts = {_host(spec.url_template) for spec in existing_specs}
    existing_hosts.update(
        str(host).strip().lower().removeprefix("www.") for host in extra_hosts
    )
    existing_hosts.discard("")
    result: list[SiteSpec] = []
    seen_keys: set[str] = set()
    seen_hosts: set[str] = set()

    for item in payload.get("sites") or []:
        if item.get("valid") is False or item.get("protection"):
            continue
        category = str(item.get("cat") or "misc").lower()
        if category == "xx NSFW xx" or category not in _ALLOWED_CATEGORIES:
            continue
        if item.get("e_code") != 200 or item.get("m_code") not in (200, 404, 410):
            continue
        e_string = str(item.get("e_string") or "")
        m_string = str(item.get("m_string") or "")
        url_template = str(item.get("uri_check") or "").replace("{account}", "{u}")
        name = str(item.get("name") or "").strip()
        if not e_string or not m_string or not name or "{u}" not in url_template:
            continue
        if not url_template.startswith(("http://", "https://")):
            continue
        host = _host(url_template)
        if not host or host in existing_hosts or host in seen_hosts:
            continue
        seen_hosts.add(host)

        base_key = f"wmn_{_slug(name)}"
        key = base_key
        if key in seen_keys:
            suffix = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
            key = f"{base_key}_{suffix}"
        seen_keys.add(key)

        absent_statuses: tuple[int, ...] = (404, 410)
        if item.get("m_code") in (404, 410):
            absent_statuses = (int(item["m_code"]),)
        result.append(
            SiteSpec(
                key=key,
                name=name,
                platform=Platform.WEB,
                url_template=url_template,
                absent_statuses=absent_statuses,
                expect_status=200,
                present_regex=re.escape(e_string),
                absent_regex=re.escape(m_string),
                require_username=False,
                id_namespace=key,
                site_tier=2 if category in _TIER2_CATEGORIES else 3,
                notes=f"WhatsMyName CC BY-SA 4.0 ({category})",
            )
        )

    result.sort(key=lambda spec: (spec.site_tier, spec.key))
    return result


WMN_SITES: list[SiteSpec] = []

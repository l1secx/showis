"""WhatsMyName turetilmis verisini (A1) ``SiteSpec``'lere baglayan yukleyici.

``data/wmn-sites.json`` (bkz. ayni dizindeki ``NOTICE.md``; CC BY-SA 4.0
turetilmis veri) Showis'in elle yazilmis ``SITES`` listesine **ek** kapsama
saglar. Bu modul uc isi yapar:

1. **Yukleme** — ``load_wmn_specs()``: JSON kayitlarini dogrulanmis
   ``SiteSpec`` nesnelerine cevirir, bozuk kaydi atlar ve uyarir. Basliklar
   ikinci kez **allowlist**'ten gecirilir (savunmaci; importer ayni listeyi
   uygular).
2. **Birlestirme** — ``merged_sites()``: elle yazilmis ``SITES`` **once** gelir,
   ardindan host bazinda tekil WMN kayitlari. Cakisan host'ta elle yazilmis
   kazanir. NSFW kategorisi varsayilan olarak elenir
   (``SHOWIS_SITECHECK_ALLOW_NSFW=1`` ile acilir).
3. **Butce** — ``sites_for_username()``: mod basina **istek butcesi**
   (``SITECHECK_REQUEST_BUDGET``) ve planli kullanici adi sayisindan turetilen
   site limitini uygular; WMN dilimini kullanici adina gore **deterministik**
   dondurur (round-robin). Boylece tek bir kullanici adi tum butceyi yiyip
   digerlerini ac birakmaz.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from showis_contracts import Platform
from showis_engine.scan_modes import norm_mode

from .sites import SITES, SiteSpec

logger = logging.getLogger("showis.sitecheck.wmn")

#: Turetilmis veri dosyasi (importer ciktisi; elle duzenlenmez).
WMN_PATH = Path(__file__).with_name("data") / "wmn-sites.json"

#: WMN'de yetiskin icerik kategorisi.
NSFW_CATEGORY = "xx NSFW xx"

#: ``SiteSpec.notes`` icinde WMN kaynagini isaretleyen deger.
WMN_SOURCE_NOTE = "wmn"

#: Aktarilabilecek istek basliklari (D4: allowlist; fail-closed).
#: Kimlik/oturum tasiyabilecek basliklar (``Cookie``, ``Authorization``,
#: ``X-*``, ``Referer``) hicbir kosulda tasinmaz.
ALLOWED_HEADERS = frozenset({"accept", "accept-language"})

#: Mod basina sitecheck **istek** butcesi (D2). 0 = WMN/sitecheck kapali.
SITECHECK_REQUEST_BUDGET: dict[str, int] = {"O1": 0, "O2": 600, "O3": 1200}

#: Env ile mod butcesini ezme (gecersiz/negatif deger -> mod varsayilani).
REQUEST_BUDGET_ENV = "SHOWIS_SITECHECK_REQUEST_BUDGET"

#: NSFW kategorisini taramaya dahil etme anahtari.
ALLOW_NSFW_ENV = "SHOWIS_SITECHECK_ALLOW_NSFW"

_TRUTHY = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Yukleme
# ---------------------------------------------------------------------------


def _host(url_template: str) -> str:
    """Sablonun host'u (kucuk harf, ``www.`` atilmis)."""
    host = (urlsplit(url_template.replace("{u}", "ornek")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def filter_headers(raw: Any) -> dict[str, str]:
    """Basliklari allowlist'ten gecirir (savunmaci; importer ile ayni kural)."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): str(value)
        for name, value in raw.items()
        if str(name).strip().lower() in ALLOWED_HEADERS and str(value)
    }


def _spec_from_record(record: Any, position: int) -> SiteSpec:
    """Tek JSON kaydini ``SiteSpec``'e cevirir; gecersizse ``ValueError``."""
    if not isinstance(record, dict):
        raise ValueError("kayit nesne degil")

    key = str(record.get("key") or "").strip()
    name = str(record.get("name") or "").strip()
    template = str(record.get("url_template") or "").strip()
    if not key:
        raise ValueError("key bos")
    if not template.startswith("https://") or "{u}" not in template:
        raise ValueError(f"url_template gecersiz ({template!r})")

    expect = record.get("expect_status")
    if not isinstance(expect, int) or isinstance(expect, bool):
        raise ValueError(f"expect_status int degil ({expect!r})")

    statuses = record.get("absent_statuses")
    if statuses is None:
        statuses = []
    if not isinstance(statuses, list) or not all(
        isinstance(s, int) and not isinstance(s, bool) for s in statuses
    ):
        raise ValueError(f"absent_statuses int listesi degil ({statuses!r})")

    return SiteSpec(
        key=key,
        name=name or key,
        platform=Platform.WEB,
        url_template=template,
        absent_statuses=tuple(int(s) for s in statuses),
        expect_status=int(expect),
        present_regex=record.get("present_regex") or None,
        absent_regex=record.get("absent_regex") or None,
        # WMN literal substring'leri present_regex'te; eslesmezse "unknown"
        # kalir (yanlis pozitif uretmemek icin varsayilan).
        no_match_is_absent=False,
        headers=filter_headers(record.get("headers")),
        notes=WMN_SOURCE_NOTE,
        category=str(record.get("category") or "").strip(),
    )


@lru_cache(maxsize=1)
def load_wmn_specs() -> tuple[SiteSpec, ...]:
    """WMN turetilmis verisini yukler (onbellekli).

    Dosya yoksa/bozuksa bos tuple doner (kapsama sessizce daralir, tarama
    bozulmaz). Tekil olmayan ``key`` kayitlari atlanir.
    """
    if not WMN_PATH.exists():
        logger.warning("WMN verisi yok, atlanıyor: %s", WMN_PATH)
        return ()

    try:
        payload = json.loads(WMN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("WMN verisi okunamadi (%s): %s", WMN_PATH, exc)
        return ()

    records = payload.get("sites") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        logger.warning("WMN verisi beklenen semada degil (sites listesi yok): %s", WMN_PATH)
        return ()

    specs: list[SiteSpec] = []
    seen: set[str] = set()
    for position, record in enumerate(records):
        try:
            spec = _spec_from_record(record, position)
        except ValueError as exc:
            label = record.get("key") if isinstance(record, dict) else position
            logger.warning("WMN kaydi atlandi (%s): %s", label, exc)
            continue
        if spec.key in seen:
            logger.warning("WMN kaydi atlandi (%s): key tekrari", spec.key)
            continue
        seen.add(spec.key)
        specs.append(spec)

    logger.info("WMN verisi yuklendi: %d spec (%s)", len(specs), WMN_PATH.name)
    return tuple(specs)


def clear_cache() -> None:
    """Onbellegi temizler (testlerde dosya degistiginde kullanilir)."""
    load_wmn_specs.cache_clear()


# ---------------------------------------------------------------------------
# Politika / butce
# ---------------------------------------------------------------------------


def allow_nsfw() -> bool:
    """NSFW kategorisi taransin mi (varsayilan: hayir)."""
    return os.getenv(ALLOW_NSFW_ENV, "").strip().lower() in _TRUTHY


def spec_category(spec: SiteSpec) -> str | None:
    """``SiteSpec`` kategorisi (elle yazilmissa ``None``)."""
    return spec.category or None


def is_wmn(spec: SiteSpec) -> bool:
    """Spec turetilmis WMN verisinden mi?"""
    return spec.notes == WMN_SOURCE_NOTE


def is_nsfw(spec: SiteSpec) -> bool:
    return spec_category(spec) == NSFW_CATEGORY


def request_budget_for_mode(mode: str | None) -> int:
    """Moda gore sitecheck istek butcesi (env ile ezilebilir).

    Gecersiz/negatif env degeri mod varsayilanina doner (fail-safe).
    """
    raw = (os.getenv(REQUEST_BUDGET_ENV) or "").strip()
    if raw:
        try:
            value = int(raw, 10)
        except ValueError:
            logger.warning(
                "%s gecersiz (%r); mod varsayilani kullanilacak", REQUEST_BUDGET_ENV, raw
            )
        else:
            if value >= 0:
                return value
            logger.warning(
                "%s negatif (%d); mod varsayilani kullanilacak", REQUEST_BUDGET_ENV, value
            )
    return SITECHECK_REQUEST_BUDGET[norm_mode(mode)]


def site_limit_for(usernames: int, mode: str | None) -> int:
    """Kullanici adi basina site limiti (``budget // n``, en az 1).

    ``n`` planlanan sitecheck kullanici adi sayisidir; boylece
    ``n x site_limit <= budget`` garanti edilir (bolme kalani kadar sapma).
    """
    budget = request_budget_for_mode(mode)
    if budget <= 0:
        return 0
    return max(1, budget // max(1, int(usernames)))


# ---------------------------------------------------------------------------
# Birlestirme
# ---------------------------------------------------------------------------


def _wmn_extra(manual: list[SiteSpec]) -> list[SiteSpec]:
    """Elle yazilanlarla cakismayan WMN spec'leri (host ve key bazinda)."""
    hosts = {_host(spec.url_template) for spec in manual}
    keys = {spec.key for spec in manual}
    nsfw_allowed = allow_nsfw()
    extra: list[SiteSpec] = []
    for spec in load_wmn_specs():
        if not nsfw_allowed and is_nsfw(spec):
            continue
        host = _host(spec.url_template)
        if host in hosts or spec.key in keys:
            # Ayni host iki kez planlanmaz; elle yazilmis spec kazanir.
            continue
        hosts.add(host)
        keys.add(spec.key)
        extra.append(spec)
    return extra


def split_sites() -> tuple[list[SiteSpec], list[SiteSpec]]:
    """``(elle_yazilmis, wmn_ek)`` ikilisi (NSFW politikasi uygulanmis)."""
    manual = list(SITES)
    return manual, _wmn_extra(manual)


def merged_sites() -> list[SiteSpec]:
    """Elle yazilmis + WMN (host tekilleştirilmis, NSFW filtrelenmis) listesi."""
    manual, extra = split_sites()
    return manual + extra


# ---------------------------------------------------------------------------
# Butce dilimi
# ---------------------------------------------------------------------------


def _wmn_share(total_limit: int, available_wmn: int, manual_count: int) -> int:
    """Site limiti icinde WMN'e ayrilan pay (E1).

    Kural: ``max(20, limit // 3)``, WMN listesi veya limitin kendisi kadar
    kirpilir. Boylece elle yazilmis kuratorlu siteler silinmez (limit >= 40
    olan gercek taramalarda en az ``limit - wmn_share`` site kalir) ve WMN
    kapsamasi tamamen sifirlanmaz.
    """
    if total_limit <= 0 or available_wmn <= 0 or manual_count <= 0:
        return 0
    share = max(20, total_limit // 3)
    # Cok kucuk limitlerde (env ile dusurulurse) kuratorlu siteler yaridan aza
    # inmesin; gercek modlarda (limit >= 60) bu kirpma devreye girmez.
    share = min(share, max(0, total_limit - min(manual_count, total_limit // 2)))
    return min(share, available_wmn, total_limit)


def sites_for_username(
    username: str,
    mode: str | None = None,
    limit: int | None = None,
) -> list[SiteSpec]:
    """Bir kullanici adi icin taranacak siteler.

    * ``limit`` = bu kullanici adi icin **toplam** site tavani (elle yazilmis
      dahil). Verilmezse mod butcesinden turetilir (tek kullanici adi varsayimi).
    * Limit, elle yazilmis ile WMN arasinda **paylastirilir**: WMN payi
      ``max(20, limit // 3)`` (``_wmn_share``); kalan yer elle yazilmis
      sitelere ayrilir.
    * WMN dilimi ``sha1(username)`` offset'i ile dondurulur: ayni ad -> ayni
      dilim (deterministik), farkli adlar -> farkli dilimler (round-robin).
    * ``limit == 0`` (orn. O1 butcesi) -> bos liste; istek atilmaz.
    """
    manual, extra = split_sites()
    total_limit = request_budget_for_mode(mode) if limit is None else int(limit)
    if total_limit <= 0:
        return []

    share = _wmn_share(total_limit, len(extra), len(manual))
    manual_take = min(len(manual), max(0, total_limit - share))
    wmn_take = min(total_limit - manual_take, len(extra))
    if wmn_take <= 0:
        return manual[:manual_take]

    digest = hashlib.sha1(str(username or "").encode("utf-8")).hexdigest()
    offset = int(digest, 16) % len(extra)
    rotated = [extra[(offset + i) % len(extra)] for i in range(wmn_take)]
    return manual[:manual_take] + rotated

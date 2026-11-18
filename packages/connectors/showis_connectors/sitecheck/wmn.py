"""WhatsMyName turetilmis verisini (A1) ``SiteSpec``'lere baglayan yukleyici.

``data/wmn-sites.json`` (bkz. ayni dizindeki ``NOTICE.md``; CC BY-SA 4.0
turetilmis veri) Showis'in elle yazilmis ``SITES`` listesine **ek** kapsama
saglar. Bu modul uc isi yapar:

1. **Yukleme** — ``load_wmn_specs()``: JSON kayitlarini dogrulanmis
   ``SiteSpec`` nesnelerine cevirir, bozuk kaydi atlar ve uyarir.
2. **Birlestirme** — ``merged_sites()``: elle yazilmis ``SITES`` **once** gelir,
   ardindan host bazinda tekil WMN kayitlari. Cakisan host'ta elle yazilmis
   kazanir (ayarli sinyaller daha iyi). NSFW kategorisi varsayilan olarak
   elenir (``SHOWIS_SITECHECK_ALLOW_NSFW=1`` ile acilir).
3. **Butce dilimi** — ``sites_for_username()``: mod basina WMN tavanini uygular
   ve site listesini kullanici adina gore **deterministik** dondurur. Boylece
   tek bir kullanici adi tum listeyi yiyip diger kullanicilari ac birakmaz.

WMN kategorisi ``SiteSpec``'te alan olmadigi icin ``notes`` alanina
``"wmn:<kategori>"`` olarak yazilir; ``spec_category()`` bunu okur.
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

#: ``SiteSpec.notes`` icinde WMN kaynagini/kategorisini isaretleyen onek.
WMN_NOTES_PREFIX = "wmn:"

#: Mod basina WMN site tavani (elle yazilmis ``SITES`` her modda dahildir).
#: 0 = WMN kapali (yalniz elle yazilmis siteler).
SITE_CAP_BY_MODE: dict[str, int] = {"O1": 0, "O2": 120, "O3": 150}

#: Env ile mod tavanini ezme (negatif/gecersiz deger -> mod varsayilani).
MAX_SITES_ENV = "SHOWIS_SITECHECK_MAX_SITES"

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

    headers = record.get("headers")
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise ValueError("headers nesne degil")

    category = str(record.get("category") or "misc").strip() or "misc"

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
        # kalir (posing riskini artirmamak icin varsayilan).
        no_match_is_absent=False,
        headers={str(k): str(v) for k, v in headers.items()},
        notes=f"{WMN_NOTES_PREFIX}{category}",
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
# Politika / tavan
# ---------------------------------------------------------------------------


def allow_nsfw() -> bool:
    """NSFW kategorisi taransin mi (varsayilan: hayir)."""
    return os.getenv(ALLOW_NSFW_ENV, "").strip().lower() in _TRUTHY


def spec_category(spec: SiteSpec) -> str | None:
    """``SiteSpec`` icin WMN kategorisi (elle yazilmissa ``None``)."""
    notes = spec.notes or ""
    if not notes.startswith(WMN_NOTES_PREFIX):
        return None
    return notes[len(WMN_NOTES_PREFIX) :] or None


def is_nsfw(spec: SiteSpec) -> bool:
    return spec_category(spec) == NSFW_CATEGORY


def site_cap_for_mode(mode: str | None) -> int:
    """Mod basina WMN site tavani (env ile ezilebilir; gecersiz -> varsayilan)."""
    raw = (os.getenv(MAX_SITES_ENV) or "").strip()
    if raw:
        try:
            value = int(raw, 10)
        except ValueError:
            logger.warning(
                "%s gecersiz (%r); mod varsayilani kullanilacak", MAX_SITES_ENV, raw
            )
        else:
            if value >= 0:
                return value
            logger.warning(
                "%s negatif (%d); mod varsayilani kullanilacak", MAX_SITES_ENV, value
            )
    return SITE_CAP_BY_MODE[norm_mode(mode)]


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


def sites_for_username(username: str, mode: str | None = None) -> list[SiteSpec]:
    """Bir kullanici adi icin taranacak siteler.

    * Elle yazilmis ``SITES`` **her zaman** dahildir.
    * WMN kismi mod tavanina gore dilimlenir (``SITE_CAP_BY_MODE`` / env).
    * Dilim, kullanici adinin sha1'ine gore dondurulur: ayni ad -> ayni dilim
      (deterministik), farkli adlar -> farkli dilimler (round-robin).
    """
    manual, extra = split_sites()
    cap = site_cap_for_mode(mode)
    if cap <= 0 or not extra:
        return manual

    count = min(cap, len(extra))
    digest = hashlib.sha1(str(username or "").encode("utf-8")).hexdigest()
    offset = int(digest, 16) % len(extra)
    rotated = [extra[(offset + i) % len(extra)] for i in range(count)]
    return manual + rotated

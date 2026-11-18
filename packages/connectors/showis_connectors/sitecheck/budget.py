"""Site-check **aile** istek butcesi (D10).

`sitecheck` (birincil) ve `sitecheck_search` (arama) ayni tarama butcesini
paylasir: ikisi ayri ayri butce planlarsa toplam, tarama butcesini asar
(Z3 denetimi: O2'de 1200, O3'te 2400 istek).

Model (**istek** birimi — Z5-QA-01):

* ``SITECHECK_FAMILY_BUDGET`` — ailenin mod basina **toplam istek** tavani
  (O1=0, O2=600, O3=1200). ``SHOWIS_SITECHECK_REQUEST_BUDGET`` env ile ezilir;
  deger **modun `max_http_requests`'inin yarisini** asamaz (O1=250, O2=600,
  O3=1000) — sitecheck tek basina tarama butcesini yiyemez (Z5-QA-02).
* Paylar: **%80 birincil / %20 arama** (O2: 480 + 120, O3: 960 + 240).
* Her yeni origin icin **1 robots.txt** istegi de gercek trafige girer. Bu
  yuzden site basina istek carpani ``1 + ROBOTS_FACTOR`` (1,3) ile hesaplanir:
  ``site_limit_for(share, n) = share // ((1+0.3) x n)``. Boylece
  ``n x site x 1,3 <= share`` (planlanan **istek** <= pay) **her zaman** saglanir.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from showis_engine.scan_modes import MODE_PROFILES, norm_mode

logger = logging.getLogger("showis.sitecheck.budget")

#: Ailenin mod basina toplam istek butcesi (D10).
SITECHECK_FAMILY_BUDGET: dict[str, int] = {"O1": 0, "O2": 600, "O3": 1200}

#: Pay dagilimi (toplam 1.0).
PRIMARY_SHARE = 0.80
SEARCH_SHARE = 0.20

#: Aile butcesini ezen env anahtari (int >= 0; gecersiz/negatif -> varsayilan).
REQUEST_BUDGET_ENV = "SHOWIS_SITECHECK_REQUEST_BUDGET"

#: Env ile ezilebilecek ust sinir: modun tarama butcesinin bu kadari.
ENV_MAX_FRACTION = 0.5

#: Site basina ek **robots.txt** istegi (olculen 1,23-1,30x trafigin ust siniri).
#: Plan ve etiket ayni carpani kullanir (tek kaynak).
ROBOTS_FACTOR = 0.3

Role = Literal["primary", "search"]


def family_budget(mode: str | None) -> int:
    """Modun sitecheck **aile** istek butcesi (env ile ezilebilir, kirpilmis)."""
    normalized = norm_mode(mode)
    default = SITECHECK_FAMILY_BUDGET[normalized]
    max_requests = MODE_PROFILES[normalized]["max_http_requests"]

    raw = (os.getenv(REQUEST_BUDGET_ENV) or "").strip()
    if raw:
        try:
            value = int(raw, 10)
        except ValueError:
            logger.warning(
                "%s gecersiz (%r); mod varsayilani kullanilacak", REQUEST_BUDGET_ENV, raw
            )
        else:
            if value < 0:
                logger.warning(
                    "%s negatif (%d); mod varsayilani kullanilacak",
                    REQUEST_BUDGET_ENV,
                    value,
                )
            else:
                ceiling = env_ceiling(normalized)
                if value > ceiling:
                    logger.warning(
                        "%s=%d ust siniri (%d) asiyor; kirpildi",
                        REQUEST_BUDGET_ENV,
                        value,
                        ceiling,
                    )
                return min(value, ceiling)
    return min(default, max_requests)


def env_ceiling(mode: str | None) -> int:
    """Env override'in mod basina ust siniri (tarama butcesinin %50'si)."""
    max_requests = MODE_PROFILES[norm_mode(mode)]["max_http_requests"]
    return int(max_requests * ENV_MAX_FRACTION)


def share_budget(mode: str | None, role: Role) -> int:
    """Aile butcesinin role dusen payi (primary %80 / search %20)."""
    if role not in ("primary", "search"):
        raise ValueError(f"bilinmeyen pay: {role!r}")
    total = family_budget(mode)
    share = PRIMARY_SHARE if role == "primary" else SEARCH_SHARE
    return int(total * share)


def site_limit_for(share: int, usernames: int) -> int:
    """Pay basina **kullanici adi basina** site limiti (robots dahil istek).

    ``n x site x (1 + ROBOTS_FACTOR) <= share`` degismezi korunur:
    ``share // ((1+0.3) x n)``. Pay yetmezse 0 doner (istek atilmaz; ``max(1,
    ...)`` degismezi bozardi — bkz. Z3 M1).
    """
    share = max(0, int(share))
    usernames = int(usernames)
    if usernames <= 0:
        return share
    request_cost = (1.0 + ROBOTS_FACTOR) * usernames
    limit = int(share // request_cost)
    if limit <= 0:
        logger.warning(
            "Site-check butcesi (%d) %d kullanici adi + robots icin yetersiz; "
            "istek planlanmiyor",
            share,
            usernames,
        )
        return 0
    return limit


def planned_requests(sites: int) -> int:
    """``sites`` profil kontrolunun **robots dahil** istek karsiligi."""
    return int(round(max(0, int(sites)) * (1.0 + ROBOTS_FACTOR)))


def primary_site_limit(mode: str | None, usernames: int) -> int:
    """Birincil connector icin kullanici adi basina site limiti."""
    return site_limit_for(share_budget(mode, "primary"), usernames)


def search_site_limit(mode: str | None, usernames: int) -> int:
    """Arama connector'i icin kullanici adi basina site limiti."""
    return site_limit_for(share_budget(mode, "search"), usernames)

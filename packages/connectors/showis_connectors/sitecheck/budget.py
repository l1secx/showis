"""Site-check **aile** istek butcesi (D10).

`sitecheck` (birincil) ve `sitecheck_search` (arama) ayni tarama butcesini
paylasir: ikisi ayri ayri butce planlarsa toplam, tarama butcesini asar
(Z3 denetimi: O2'de 1200, O3'te 2400 istek).

Model:

* ``SITECHECK_FAMILY_BUDGET`` — ailenin mod basina **toplam** istek tavani
  (O1=0, O2=600, O3=1200). ``SHOWIS_SITECHECK_REQUEST_BUDGET`` env ile ezilir;
  deger mod profilinin ``max_http_requests``'ini asamaz (kapasite iddiasi
  yaniltmasin).
* Paylar: **%80 birincil / %20 arama** (O2: 480 + 120, O3: 960 + 240).
* ``site_limit_for(share, n)`` = ``share // n`` (n>0). Boylece
  ``n x site_limit <= share`` **her zaman** saglanir; ``share < n`` iken 0
  doner (istek atilmaz — fail-closed, degismezi bozmamak icin).
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
                if value > max_requests:
                    logger.warning(
                        "%s=%d tarama butcesini (%d) asiyor; kirpildi",
                        REQUEST_BUDGET_ENV,
                        value,
                        max_requests,
                    )
                return min(value, max_requests)
    return min(default, max_requests)


def share_budget(mode: str | None, role: Role) -> int:
    """Aile butcesinin role dusen payi (primary %80 / search %20)."""
    if role not in ("primary", "search"):
        raise ValueError(f"bilinmeyen pay: {role!r}")
    total = family_budget(mode)
    share = PRIMARY_SHARE if role == "primary" else SEARCH_SHARE
    return int(total * share)


def site_limit_for(share: int, usernames: int) -> int:
    """Pay basina **kullanici adi basina** site limiti.

    ``n x limit <= share`` degismezi korunur: ``share // n``. Pay, kullanici
    adi sayisindan kucukse 0 doner (istek atilmaz; ``max(1, ...)`` degismezi
    bozardi — bkz. Z3 M1).
    """
    share = max(0, int(share))
    usernames = int(usernames)
    if usernames <= 0:
        return share
    if share < usernames:
        logger.warning(
            "Site-check butcesi (%d) kullanici adi sayisindan (%d) kucuk; "
            "istek planlanmiyor",
            share,
            usernames,
        )
        return 0
    return share // usernames


def primary_site_limit(mode: str | None, usernames: int) -> int:
    """Birincil connector icin kullanici adi basina site limiti."""
    return site_limit_for(share_budget(mode, "primary"), usernames)


def search_site_limit(mode: str | None, usernames: int) -> int:
    """Arama connector'i icin kullanici adi basina site limiti."""
    return site_limit_for(share_budget(mode, "search"), usernames)

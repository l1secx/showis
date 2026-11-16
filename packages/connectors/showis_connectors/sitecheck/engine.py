"""Username-check tarama motoru.

SiteSpec listesindeki her site icin tek bir GET istegi atar ve
varlik kararini cok-sinyalli olarak verir. Sonuclar "exists" |
"absent" | "unknown" | "error" | "skipped" durumlarindan biridir;
yalnizca pozitif sinyalde "exists" uretilir.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from showis_engine.http import HttpClient

from .cache import CheckCache
from .sites import SITES, SiteSpec

logger = logging.getLogger("showis.sitecheck")

#: Sonuc durumlari
EXISTS = "exists"
ABSENT = "absent"
UNKNOWN = "unknown"
ERROR = "error"
SKIPPED = "skipped"

_SAMPLE_LIMIT = 8192  # evidence icin saklanan govde ornegi (byte)
#: absent/present regex'lerin tarayacagi maksimum govde boyutu.
#: Buyuk SPA sayfalarinda "not found" isareti 8KB ornegini asabilir
#: (orn. Pinterest ~1.2MB); siniflandirma daha genis pencerede yapilir.
_SCAN_LIMIT = 1_048_576


@dataclass
class CheckResult:
    site: SiteSpec
    username: str
    status: str
    url: str
    http_status: int | None = None
    detail: str = ""
    #: exists durumunda saklanacak kisa govde ornegi (bytes)
    body_sample: bytes | None = None
    final_url: str | None = None
    #: True ise sonuc onbellekten geldi (yeni ag istegi atilmadi)
    from_cache: bool = False

    @property
    def exists(self) -> bool:
        return self.status == EXISTS


class UsernameCheckEngine:
    """Toplu kullanici-adi kontrolu.

    ``concurrency`` ile es zamanli istek sayisi sinirlanir; her siteye
    en fazla bir istek atilir (site bazli tek deneme — tekrar yok).
    """

    def __init__(
        self,
        http_client: HttpClient | None = None,
        sites: list[SiteSpec] | None = None,
        concurrency: int = 6,
        cache: CheckCache | None = None,
    ):
        self.http = http_client or HttpClient(enable_ssrf_guard=True)
        self.sites = sites if sites is not None else SITES
        self._sem = asyncio.Semaphore(concurrency)
        #: None -> onbellek yok (testlerde deterministik davranis).
        self.cache = cache

    # ------------------------------------------------------------------
    # Genel API
    # ------------------------------------------------------------------

    async def check_username(
        self,
        username: str,
        sites: list[SiteSpec] | None = None,
        use_cache: bool = True,
    ) -> list[CheckResult]:
        """Tek kullanici adi icin tum (veya verilen) siteleri kontrol eder."""
        targets = sites if sites is not None else self.sites
        results = await asyncio.gather(
            *(self._check_one(username, spec, use_cache=use_cache) for spec in targets)
        )
        return list(results)

    # ------------------------------------------------------------------
    # Ic mekanizma
    # ------------------------------------------------------------------

    async def _check_one(
        self, username: str, spec: SiteSpec, use_cache: bool = True
    ) -> CheckResult:
        if use_cache and self.cache is not None:
            cached = self.cache.get(username, spec.key)
            if cached is not None:
                return CheckResult(
                    site=spec,
                    username=username,
                    status=cached.status,
                    url=cached.url,
                    http_status=cached.http_status,
                    detail=cached.detail,
                    body_sample=None,
                    final_url=cached.final_url,
                    from_cache=True,
                )

        result = await self._fetch_and_classify(username, spec)
        if use_cache and self.cache is not None:
            # Yalnizca kararli sonuclar saklanir (error/unknown haric).
            self.cache.set(username, spec.key, result)
        return result

    async def _fetch_and_classify(self, username: str, spec: SiteSpec) -> CheckResult:
        url = spec.url_template.format(u=username)
        async with self._sem:
            try:
                resp = await self.http.get(
                    url,
                    headers=spec.headers or None,
                    check_robots=not spec.skip_robots,
                    follow_redirects=True,
                    max_retries=1,
                )
            except PermissionError:
                return CheckResult(
                    site=spec, username=username, status=SKIPPED,
                    url=url, detail="robots.txt engelledi",
                )
            except httpx.HTTPError as exc:
                # DNS hatasi / baglanti hatasi: siteyi "yok" saymayiz
                return CheckResult(
                    site=spec, username=username, status=ERROR,
                    url=url, detail=f"{type(exc).__name__}: {exc}",
                )
            except Exception as exc:
                logger.warning("Site-check hatasi %s/%s: %s", spec.key, username, exc)
                return CheckResult(
                    site=spec, username=username, status=ERROR,
                    url=url, detail=str(exc),
                )

        return self._classify(spec, username, url, resp)

    def _classify(
        self, spec: SiteSpec, username: str, url: str, resp: httpx.Response
    ) -> CheckResult:
        status = resp.status_code
        final_url = str(resp.url)
        body = resp.content
        text = resp.text[:_SCAN_LIMIT] if resp.content else ""

        def res(
            st: str, detail: str = "", sample: bytes | None = None
        ) -> CheckResult:
            return CheckResult(
                site=spec,
                username=username,
                status=st,
                url=url,
                http_status=status,
                detail=detail,
                body_sample=sample,
                final_url=final_url,
            )

        # 1) Acik "yok" durum kodlari
        if status in spec.absent_statuses:
            return res(ABSENT, f"HTTP {status}")

        # 2) Beklenen durum disinda -> karar veremeyiz (JSON govdesinden ONCE:
        #    403/429 hata govdesi "dolu" gorunup yanlis-pozitif uretmesin)
        if spec.expect_status is not None and status != spec.expect_status:
            return res(UNKNOWN, f"HTTP {status}")

        # 3) JSON varlik kontrolu (json_path tanimliysa)
        if spec.json_path is not None:
            data: Any = None
            try:
                data = resp.json()
            except Exception:
                return res(ERROR, "JSON parse hatasi")
            value = self._json_lookup(data, spec.json_path)
            if value:
                sample = body[:_SAMPLE_LIMIT] if spec.json_path else None
                return res(EXISTS, f"json:{spec.json_path or '$'}", sample)
            return res(ABSENT, "json alani bos")

        # 4) HTML isaretleri
        if spec.absent_regex and re.search(spec.absent_regex, text, re.IGNORECASE):
            return res(ABSENT, "absent_regex eslesti")
        if spec.present_regex and re.search(spec.present_regex, text, re.IGNORECASE):
            return res(EXISTS, "present_regex eslesti", body[:_SAMPLE_LIMIT])

        # 5) present_regex tanimliysa eslesme sart.
        #    Hic present_regex yoksa (status/absent_regex tabanli) 200 = var.
        if spec.present_regex is not None:
            if spec.no_match_is_absent:
                return res(ABSENT, "present_regex eslesmedi")
            return res(UNKNOWN, "present_regex eslesmedi")
        return res(EXISTS, "HTTP 200", body[:_SAMPLE_LIMIT])

    @staticmethod
    def _json_lookup(data: Any, path: str) -> Any:
        """Nokta-ayracli yolda deger dondurur; '' veya '.' kok demektir."""
        if not path or path == ".":
            return data
        cur = data
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            elif isinstance(cur, list) and part.isdigit():
                idx = int(part)
                cur = cur[idx] if idx < len(cur) else None
            else:
                return None
        return cur

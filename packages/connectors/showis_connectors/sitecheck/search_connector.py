"""Site-check connector'a web arama entegrasyonu.

Ad/soyad girildiğinde sadece username varyantlarını değil, arama motorundan
gelen gerçek profile linklerini de taramaya dahil eder. SearchProvider'dan
gelen sonuçlardan platform + identifier çıkarılır, sitecheck engine'e
username olarak geçilir.

Bu, kullanıcının doğrudan username bilmediği durumlarda bile (yalnızca
isim/soyisim ile) gerçek OSINT sonuçları üretmesini sağlar.
"""

from __future__ import annotations

import logging

from showis_contracts import ScanContext, TaskSpec
from showis_engine.planner import sanitize_hint
from showis_engine.policy import PolicyViolationError, guard_query

from showis_connectors.websearch import SearchProvider, extract_platform_from_url

from .connector import SiteCheckConnector
from .engine import normalize_username_for_url

logger = logging.getLogger("showis.sitecheck.search")


class SiteCheckSearchConnector(SiteCheckConnector):
    """Site-check motoru + plan aşamasında web araması.

    Tarama/normalize mantığı tamamen SiteCheckConnector'dadır; burada yalnızca
    plana arama sonuçlarından gelen ek username'ler eklenir.
    """

    name: str = "sitecheck_search"
    supported_kinds: set[str] = {"search_and_check"}
    detection: str = "sitecheck_search"

    def __init__(
        self,
        *,
        search_provider: SearchProvider | None = None,
        max_usernames: int = 10,
        max_search_usernames: int = 10,
        **kwargs,
    ):
        super().__init__(max_usernames=max_usernames, **kwargs)
        self.search_provider = search_provider
        #: SERP'ten gelen ek kimlikler için tavan (plan/bütçe tahminiyle uyumlu:
        #: scan_modes._SITECHECK_SEARCH_USERNAMES = 10).
        self.max_search_usernames = max(0, int(max_search_usernames))

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks = await super().plan(ctx)
        if not self.search_provider:
            return tasks

        fullname = sanitize_hint(f"{ctx.target.first_name} {ctx.target.last_name}")
        nick = sanitize_hint((ctx.target.nickname or "").strip().lstrip("@"))
        subject = fullname or nick
        if not subject:
            return tasks

        search_query = f'"{subject}"'
        try:
            guard_query(search_query)
        except PolicyViolationError:
            return tasks

        seen = {str(t.params.get("username") or "") for t in tasks}
        try:
            # Tirnakli arama: ayni adi tasiyan ilgisiz kisileri ele
            hits = await self.search_provider.search(
                query=search_query, limit=30, site=None
            )
        except Exception as exc:
            logger.warning("Arama sağlayıcısı başarısız oldu: %s", exc)
            return tasks

        added = 0
        for hit in hits:
            if added >= self.max_search_usernames:
                break
            _, ident = extract_platform_from_url(hit.url)
            # T-365: arama sonucu kimligi guvenilmez — URL'e konamayacak adlar
            # gorev uretmeden elenir (tek yerden: normalize_username_for_url).
            if ident and normalize_username_for_url(ident) is None:
                logger.debug("Site-check: gecersiz kimlik atlandi (%r)", ident)
                continue
            if ident and ident not in seen:
                seen.add(ident)
                if not self.plan_registry.claim(ctx.scan_id, ident, self.name):
                    continue
                tasks.append(self._make_task(ident, priority=3))
                added += 1
        return tasks

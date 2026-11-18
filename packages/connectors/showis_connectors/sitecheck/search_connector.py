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

from showis_connectors.websearch import SearchProvider, extract_platform_from_url

from .budget import search_site_limit, share_budget
from .connector import MAX_PLANNED_USERNAMES, SiteCheckConnector
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
        **kwargs,
    ):
        super().__init__(max_usernames=max_usernames, **kwargs)
        self.search_provider = search_provider

    async def plan(self, ctx: ScanContext) -> list[TaskSpec]:
        tasks = await super().plan(ctx)
        if not self.search_provider:
            return self._finalize(tasks, ctx)

        fullname = f"{ctx.target.first_name} {ctx.target.last_name}".strip()
        # Handle hedefinde ad/soyad yoktur: arama sorgusu nick'ten kurulur.
        # Nick kullanici girdisidir: tirnak enjeksiyonu notralize edilir (SEC-10).
        from showis_engine.planner import sanitize_hint

        nick = sanitize_hint((ctx.target.nickname or "").strip().lstrip("@"))
        subject = fullname or nick
        if not subject:
            return self._finalize(tasks, ctx)

        seen = {str(t.params.get("username") or "") for t in tasks}
        try:
            # Tirnakli arama: ayni adi tasiyan ilgisiz kisileri ele
            hits = await self.search_provider.search(
                query=f'"{subject}"', limit=30, site=None
            )
        except Exception as exc:
            logger.warning("Arama sağlayıcısı başarısız oldu: %s", exc)
            return self._finalize(tasks, ctx)

        for hit in hits:
            # D10: arama kaynakli kullanici adlari da plan tavanina tabi
            # (aksi halde n buyur, limit kuculur ve butce asilir).
            if len(tasks) >= MAX_PLANNED_USERNAMES:
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
        return self._finalize(tasks, ctx)

    @staticmethod
    def _site_limit(task: TaskSpec, mode: str | None = None) -> int:
        """Arama gorevlerinde limit **arama payina** kirpilir (Z5-QA-04).

        Bayat/elle verilmis (ör. birincil payla planlanmis) bir deger arama
        butcesini asarsa ``min(value, share_budget(mode, "search"))``.
        """
        cap = share_budget(mode, "search")
        raw = task.params.get("site_limit")
        if raw is None:
            return search_site_limit(mode, MAX_PLANNED_USERNAMES)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning("Gecersiz site_limit (%r), arama payi kullanilacak", raw)
            return search_site_limit(mode, MAX_PLANNED_USERNAMES)
        return min(max(0, value), cap)

    def _finalize(self, tasks: list[TaskSpec], ctx: ScanContext) -> list[TaskSpec]:
        """Tum gorevlere mod + **arama payindan** turetilen site limitini yazar.

        Z3 C1: bu connector kendi payini kullanmali; limitsiz gorev tum mod
        butcesini (592 site) tariyordu. Aile toplami = birincil %80 + arama %20
        <= `SITECHECK_FAMILY_BUDGET`.
        """
        mode = self._resolve_plan_mode(ctx)
        site_limit = search_site_limit(mode, len(tasks))
        for task in tasks:
            task.params["mode"] = mode
            task.params["site_limit"] = site_limit
        if tasks:
            logger.info(
                "Site-check arama plani: %s modu, %d kullanıcı adı × %d site "
                "(aile butcesinin arama payi)",
                mode,
                len(tasks),
                site_limit,
            )
        return tasks


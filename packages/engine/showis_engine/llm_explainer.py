import logging
import os
import time

from showis_contracts import Finding, Target
from showis_llm import LLMClient, redact_text, render_prompt

logger = logging.getLogger("showis.explainer")

#: T-333: tarama başına LLM açıklamalarına ayrılan toplam süre (sn).
#: Bütçe dolunca kalan bulgular deterministik yerel açıklamaya düşer.
BUDGET_ENV = "SHOWIS_LLM_EXPLAIN_BUDGET_SECONDS"
DEFAULT_BUDGET_SECONDS = 25.0


def _budget_seconds() -> float:
    raw = os.getenv(BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_BUDGET_SECONDS
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        logger.warning("Gecersiz %s=%r, varsayilan kullanilir", BUDGET_ENV, raw)
        return DEFAULT_BUDGET_SECONDS


class FindingExplainer:
    def __init__(
        self, llm_client: LLMClient | None = None, budget_seconds: float | None = None
    ):
        self.llm_client = llm_client
        self.budget_seconds = (
            _budget_seconds() if budget_seconds is None else max(0.0, float(budget_seconds))
        )
        #: LLM'e harcanan toplam süre ve bütçe durumu (T-333)
        self.spent_seconds = 0.0
        self.llm_disabled = False

    @property
    def budget_exhausted(self) -> bool:
        return self.spent_seconds >= self.budget_seconds

    @staticmethod
    def _authenticity_note(finding: Finding) -> str:
        """Düşük authenticity (<0.35) için deterministik uyarı cümlesi.

        ROADMAP 4.1: "gerçek ama zayıf" ile "boş/bot hesap" ayrımı. Cümle LLM'e
        gönderilmez, doğrudan bulgu açıklamasına eklenir.
        """
        score = finding.authenticity
        if score is None or score >= 0.35:
            return ""
        reasons = [
            r
            for r in (finding.authenticity_reasons or [])
            if not r.startswith("kullanılan sinyaller:")
        ][:2]
        detail = f" ({'; '.join(reasons)})" if reasons else ""
        return f" Düşük güvenilirlik: hesap boş/atıl görünüyor, authenticity {score:.2f}{detail}."

    @staticmethod
    def _target_info(target: Target) -> str:
        """LLM'e giden hedef etiketi — tek kaynak `Target.display_name`.

        `display_name` ad varsa "Ad Soyad", yoksa "@nick" verir (nick basindaki
        `@` orada normalize edilir); nick yalnizca **ad da varsa** parantez
        icinde eklenir, handle hedefinde `@nick` iki kez yazilmaz.
        """
        display = target.display_name
        if not display:
            return ""
        nick = (target.nickname or "").strip().lstrip("@")
        if display == f"@{nick}":
            # Handle hedefi: etiket zaten `@nick`; ek parantez gerekmez.
            return display
        return f"{display} (@{nick})" if nick else display

    async def explain(self, finding: Finding, target: Target) -> str:
        """Finding için Türkçe kanıt açıklaması üretir. Skoru asla değiştirmez."""
        # Profilden gelen alanlar LLM'e gitmeden once redakte edilir (SEC4)
        target_info = redact_text(f"{self._target_info(target)} ({target.hints})")
        breakdown_info = finding.breakdown.model_dump_json()
        evidence_summary = redact_text(
            f"Platform: {finding.platform}, URL: {finding.url}, Etiket: {finding.label}"
        )

        if self.llm_client is None or self.llm_disabled or self.budget_exhausted:
            return self._local_explanation(finding)

        try:
            prompt_content = render_prompt(
                "match_explainer",
                target=target_info,
                evidence=evidence_summary,
                breakdown=breakdown_info,
            )

            started = time.monotonic()
            response = await self.llm_client.complete(
                messages=[{"role": "user", "content": prompt_content}],
                temperature=0.0,
            )
            self.spent_seconds += time.monotonic() - started
            explanation = str(response).strip()
            # Authenticity uyarısı LLM'e gönderilmez; deterministik olarak eklenir.
            explanation += self._authenticity_note(finding)
            finding.explanation = explanation
            return explanation

        except Exception as e:
            self.llm_disabled = True
            logger.warning(
                "LLM Explainer hatası (%s), yerel açıklamaya dönülüyor", type(e).__name__
            )
            return self._local_explanation(finding)

    def _local_explanation(self, finding: Finding) -> str:
        """Deterministik (LLM'siz) açıklama — bütçe/başarısızlık yolunda kullanılır."""
        signals: list[str] = []
        if finding.breakdown.username_exact_match > 0:
            signals.append("kullanıcı adı çoklu platformda eşleşti")
        if finding.breakdown.handle_exact > 0:
            signals.append("kullanıcı adı tek platformda birebir eşleşti")
        if finding.breakdown.name_similarity > 0:
            signals.append("isim yüksek oranda benziyor")
        if finding.breakdown.cross_link > 0:
            signals.append("profilinde çapraz bağlantı bulundu")
        if finding.breakdown.bio_location_company > 0:
            signals.append("lokasyon veya şirket bilgisi uyumlu")
        if finding.breakdown.commit_email_or_pgp > 0:
            signals.append("commit e-postası doğrulandı")
        if finding.breakdown.avatar_exact_hash > 0:
            signals.append("avatar dosya karması birebir uyuşuyor")
        if finding.breakdown.identifier_match > 0:
            signals.append("hedef kimliği birebir eşleşti")

        if signals:
            plat = finding.platform.capitalize()
            sig = ", ".join(signals)
            explanation = f"{plat} profili {sig} nedeniyle bağlandı."
        else:
            explanation = (
                f"{finding.platform.capitalize()} profili incelendi ancak kanıt kuvveti zayıf."
            )
        explanation += self._authenticity_note(finding)
        finding.explanation = explanation
        return explanation

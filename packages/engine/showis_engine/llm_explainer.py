import logging

from showis_contracts import Finding, Target
from showis_llm import LLMClient, redact_text, render_prompt

logger = logging.getLogger("showis.explainer")


class FindingExplainer:
    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

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

    async def explain(self, finding: Finding, target: Target) -> str:
        """Finding için Türkçe kanıt açıklaması üretir. Skoru asla değiştirmez."""
        # Profilden gelen alanlar LLM'e gitmeden once redakte edilir (SEC4)
        target_info = redact_text(f"{target.first_name} {target.last_name} ({target.hints})")
        breakdown_info = finding.breakdown.model_dump_json()
        evidence_summary = redact_text(
            f"Platform: {finding.platform}, URL: {finding.url}, Etiket: {finding.label}"
        )

        if self.llm_client is None:
            # Deterministik yerel açıklama
            signals = []
            if finding.breakdown.username_exact_match > 0:
                signals.append("kullanıcı adı çoklu platformda eşleşti")
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

        try:
            prompt_content = render_prompt(
                "match_explainer",
                target=target_info,
                evidence=evidence_summary,
                breakdown=breakdown_info,
            )

            response = await self.llm_client.complete(
                messages=[{"role": "user", "content": prompt_content}],
                temperature=0.0,
            )
            explanation = str(response).strip()
            # Authenticity uyarısı LLM'e gönderilmez; deterministik olarak eklenir.
            explanation += self._authenticity_note(finding)
            finding.explanation = explanation
            return explanation

        except Exception as e:
            logger.warning("LLM Explainer hatası, yerel açıklamaya dönülüyor: %s", e)
            finding.explanation = (
                f"{finding.platform.capitalize()} profili bulundu. Güven skoru: {finding.score:.2f}"
                + self._authenticity_note(finding)
            )
            return str(finding.explanation)

"""
Pre-Filter Enricher — downstream prompt'lara pre-filter insight'ları ekler.

Kullanım:
    enricher = PreFilterEnricher(db)
    enriched = enricher.enrich_keywords(scoring_run_id, "ADS", keywords)

Her keyword dict'ine şu alanları ekler:
    - prefilter_label: str | None  (hot_sale, lead, treasure, viral vs.)
    - prefilter_data: dict | None  (H1/H2, hook, engagement_score vs.)
    - prefilter_reasoning: str | None (AI açıklaması)
"""
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.database.models import PreFilterResult


class PreFilterEnricher:
    """Pre-filter sonuçlarını keyword dict'lerine zenginleştirir."""

    def __init__(self, db: Session):
        self.db = db

    def enrich_keywords(
        self,
        scoring_run_id: int,
        channel: str,
        keywords: List[dict]
    ) -> List[dict]:
        """
        Keyword listesine pre-filter bilgilerini ekler.

        Args:
            scoring_run_id: Aktif scoring run ID
            channel: Kanal adı (ADS, SEO, SOCIAL)
            keywords: Keyword dicts listesi (her birinde 'id' olmalı)

        Returns:
            Enriched keyword dicts (orijinal + prefilter_*)
        """
        if not keywords:
            return keywords

        keyword_ids = [k["id"] for k in keywords]

        # Pre-filter sonuçlarını çek
        pf_results = (
            self.db.query(PreFilterResult)
            .filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.channel == channel,
                PreFilterResult.keyword_id.in_(keyword_ids)
            )
            .all()
        )

        # keyword_id → PreFilterResult mapping
        pf_map: Dict[int, PreFilterResult] = {
            r.keyword_id: r for r in pf_results
        }

        enriched = []
        for kw in keywords:
            kw_copy = dict(kw)  # Orijinali değiştirme
            pf = pf_map.get(kw["id"])

            if pf:
                kw_copy["prefilter_label"] = pf.label
                kw_copy["prefilter_data"] = pf.extra_data or {}
                kw_copy["prefilter_reasoning"] = pf.ai_reasoning
            else:
                kw_copy["prefilter_label"] = None
                kw_copy["prefilter_data"] = {}
                kw_copy["prefilter_reasoning"] = None

            enriched.append(kw_copy)

        return enriched

    def get_enrichment_context(
        self,
        scoring_run_id: int,
        channel: str,
        keyword_id: int
    ) -> Dict[str, Any]:
        """
        Tek keyword için pre-filter context döndürür.
        Prompt template'lere injection için kullanılır.

        Returns:
            {
                "label": "hot_sale" | None,
                "h1_suggestion": "..." | None,  (SEO)
                "h2_suggestions": [...] | None,  (SEO)
                "hook": "..." | None,             (Social)
                "scenario_note": "..." | None,    (Social)
                "engagement_score": 0.85 | None,  (Social)
                "reasoning": "..." | None,
                "is_transfer": bool,
            }
        """
        pf = (
            self.db.query(PreFilterResult)
            .filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.channel == channel,
                PreFilterResult.keyword_id == keyword_id
            )
            .first()
        )

        if not pf:
            return {
                "label": None,
                "h1_suggestion": None,
                "h2_suggestions": None,
                "hook": None,
                "scenario_note": None,
                "engagement_score": None,
                "reasoning": None,
                "is_transfer": False,
            }

        data = pf.extra_data or {}

        return {
            "label": pf.label,
            "h1_suggestion": data.get("h1_suggestion"),
            "h2_suggestions": data.get("h2_suggestions"),
            "hook": data.get("hook"),
            "scenario_note": data.get("scenario_note"),
            "engagement_score": data.get("engagement_score"),
            "reasoning": pf.ai_reasoning,
            "is_transfer": pf.transfer_channel is not None,
        }

    def build_prompt_context(
        self,
        scoring_run_id: int,
        channel: str,
        keyword_id: int
    ) -> str:
        """
        Prompt'a eklenecek pre-filter context string'i oluşturur.

        Returns:
            Prompt injection string (boş olabilir)
        """
        ctx = self.get_enrichment_context(
            scoring_run_id, channel, keyword_id
        )

        if not ctx["label"]:
            return ""

        parts = [f"\n--- PRE-FILTER INSIGHT ---"]
        parts.append(f"Label: {ctx['label']}")

        if ctx["reasoning"]:
            parts.append(f"AI Analiz: {ctx['reasoning']}")

        if ctx["h1_suggestion"]:
            parts.append(f"Önerilen H1: {ctx['h1_suggestion']}")

        if ctx["h2_suggestions"]:
            h2s = ", ".join(ctx["h2_suggestions"][:3])
            parts.append(f"Önerilen H2 başlıkları: {h2s}")

        if ctx["hook"]:
            parts.append(f"Social Hook: {ctx['hook']}")

        if ctx["scenario_note"]:
            parts.append(f"Senaryo Notu: {ctx['scenario_note']}")

        if ctx["engagement_score"] is not None:
            parts.append(f"Etkileşim Skoru: {ctx['engagement_score']}")

        if ctx["is_transfer"]:
            parts.append("Not: Bu keyword başka kanaldan transfer edilmiştir.")

        parts.append("--- / ---\n")

        return "\n".join(parts)

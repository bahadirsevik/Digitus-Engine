"""
Social Pre-Filter — Sosyal medya kanal filtreleme.

Kriterleri:
- Viralite ve etkileşim potansiyeli (engagement_score 0.0-1.0)
- Hook (kanca cümlesi) ve senaryo notu
- Label hizalaması: viral (≥0.70), moderate (≥0.40), weak (<0.40)
"""
import json
import re
from typing import List, Dict, Optional

from app.core.channel.pre_filters.base_filter import BasePreFilter
from app.core.constants import (
    SOCIAL_ENGAGEMENT_VIRAL,
    SOCIAL_ENGAGEMENT_MODERATE,
)


class SocialPreFilter(BasePreFilter):
    """Sosyal medya kanalı için AI pre-filter."""

    CHANNEL = "SOCIAL"
    PRICE_ROOTS = ("fiyat", "price", "ucret")
    _TR_CHAR_MAP = str.maketrans({
        "ı": "i", "ş": "s", "ç": "c", "ö": "o", "ü": "u", "ğ": "g"
    })

    def _normalize_kw(self, text: str) -> str:
        normalized = (text or "").lower().translate(self._TR_CHAR_MAP)
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _is_price_term(self, text: str) -> bool:
        normalized = self._normalize_kw(text)
        if not normalized:
            return False
        return any(
            token.startswith(root)
            for token in normalized.split()
            for root in self.PRICE_ROOTS
        )

    def _batch_filter(self, keywords: List[Dict]) -> List[Dict]:
        blocked = []
        to_ai = []
        for kw in keywords:
            keyword_text = str(kw.get("keyword", ""))
            if self._is_price_term(keyword_text):
                blocked.append({
                    "keyword_id": kw["id"],
                    "is_kept": False,
                    "label": "weak",
                    "ai_reasoning": "Deterministic price filter",
                    "extra_data": {"reason_code": "PRICE_TERM"},
                    "transfer_channel": None,
                    "is_fallback": False,
                })
            else:
                to_ai.append(kw)

        ai_results = super()._batch_filter(to_ai) if to_ai else []
        return blocked + ai_results

    def _build_filter_prompt(self, keywords: List[Dict]) -> str:
        keywords_json = json.dumps(
            [{"id": kw["id"], "keyword": kw["keyword"]} for kw in keywords],
            ensure_ascii=False
        )
        return f"""Sen bir sosyal medya ön filtresisin. Her anahtar kelimeyi kısa video/içerik potansiyeli açısından değerlendir.

KARAR KRİTERLERİ:

keep: Bu keyword ile güçlü bir hook (kanca cümlesi) ve kısa video senaryosu üretilebilir.
eliminate: İçerik potansiyeli düşük, kısa video formatına uygun değil.
eliminate: Fiyat/fiyatları/price/ucret odaklı sorgular sosyal havuza alınmamalı.

ENGAGEMENT SCORE KALİBRASYONU (0.0-1.0):
- 0.85-1.0: Gündem konusu, trending topic, challenge, viral potansiyel çok yüksek
- 0.70-0.84: Ürün karşılaştırma, "en iyi X" listesi, tartışma yaratan konu
- 0.40-0.69: Sıradan ürün tanıtımı, orta düzey ilgi çekici
- 0.00-0.39: Teknik terim, niş konu, düşük etkileşim potansiyeli

SADECE geçerli minified JSON döndür. Markdown, yorum veya açıklama YAZMA.
Schema:
{{"results":[{{"keyword_id":1,"decision":"keep","label":"viral","reason_code":"HIGH_ENGAGEMENT_POTENTIAL","reason":"Karsilastirma icerigi, tartisma yaratir","meta":{{"hook":"Bu urun gercekten degdi mi?","scenario_note":"Fiyat-performans karsilastirma","engagement_score":0.78}}}},{{"keyword_id":2,"decision":"eliminate","label":"weak","reason_code":"LOW_ENGAGEMENT_POTENTIAL","reason":"Teknik terim, video potansiyeli dusuk","meta":{{}}}}]}}

ZORUNLU:
- Girdideki her keyword_id çıktıda tam 1 kez olmalı.
- keyword_id integer olmalı.
- reason alanı kısa ama anlamlı olmalı (max 10 kelime).
- keep kararı verirsen hook ve scenario_note alanlarını doldur.
- engagement_score mutlaka 0.0-1.0 arası float olmalı.

keywords={keywords_json}
"""

    def _parse_ai_response(
        self, response, original: List[Dict]
    ) -> List[Dict]:
        """AI yanıtını standart formata çevirir. Dict ve list yanıtları destekler."""
        def _extract_keyword_id(item: Dict) -> Optional[int]:
            """AI farklı id alan adları döndürebilir: keyword_id, keywordId, keywordID, id."""
            for key in ("keyword_id", "keywordId", "keywordID", "id"):
                raw = item.get(key)
                if raw is None:
                    continue
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    continue
            return None

        # New standard schema
        if isinstance(response, dict) and isinstance(response.get("results"), list):
            response = response["results"]

        # Legacy schema kept/eliminated -> standard list
        if isinstance(response, dict):
            legacy_rows = []
            for item in response.get("kept", []):
                if not isinstance(item, dict):
                    continue
                legacy_rows.append({
                    "keyword_id": item.get("keyword_id", item.get("id")),
                    "decision": "keep",
                    "label": None,
                    "reason_code": item.get("reason_code", "HIGH_ENGAGEMENT_POTENTIAL"),
                    "reason": item.get("reason", ""),
                    "transfer_channel": None,
                    "meta": {
                        "hook": item.get("hook"),
                        "scenario_note": item.get("scenario_note"),
                        "engagement_score": item.get("engagement_score", 0.0),
                    },
                })
            for item in response.get("eliminated", []):
                if not isinstance(item, dict):
                    continue
                legacy_rows.append({
                    "keyword_id": item.get("keyword_id", item.get("id")),
                    "decision": "eliminate",
                    "label": "weak",
                    "reason_code": item.get("reason_code", "LOW_ENGAGEMENT_POTENTIAL"),
                    "reason": item.get("reason", ""),
                    "transfer_channel": None,
                    "meta": {},
                })
            if legacy_rows:
                response = legacy_rows

        # AI bazen list döndürebilir — field-based heuristic ile normalize et
        if isinstance(response, list):
            normalized = []
            for item in response:
                if not isinstance(item, dict):
                    continue
                if str(item.get("decision", "")).lower() in ("keep", "kept"):
                    decision = "keep"
                elif str(item.get("decision", "")).lower() in ("eliminate", "eliminated"):
                    decision = "eliminate"
                elif "is_kept" in item:
                    decision = "keep" if item["is_kept"] else "eliminate"
                else:
                    engagement_score = item.get("engagement_score")
                    if engagement_score is not None:
                        try:
                            decision = "keep" if float(engagement_score) >= SOCIAL_ENGAGEMENT_MODERATE else "eliminate"
                        except (TypeError, ValueError):
                            decision = "eliminate"
                    elif item.get("hook") and item.get("scenario_note"):
                        decision = "keep"
                    else:
                        decision = "eliminate"
                normalized.append({
                    **item,
                    "decision": decision,
                    "transfer_channel": item.get("transfer_channel"),
                    "meta": item.get("meta", {}),
                })
            response = normalized

        if not isinstance(response, list):
            return []

        results = []
        for item in response:
            if not isinstance(item, dict):
                continue
            keyword_id = _extract_keyword_id(item)
            if keyword_id is None:
                continue
            decision = str(item.get("decision", "")).lower()
            is_kept = decision in ("keep", "kept")
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            raw_score = meta.get("engagement_score", item.get("engagement_score", 0.0))
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0

            # Label: constants'taki eşiklerle hizalı
            if score >= SOCIAL_ENGAGEMENT_VIRAL:
                label = "viral"
            elif score >= SOCIAL_ENGAGEMENT_MODERATE:
                label = "moderate"
            else:
                label = "weak"

            results.append({
                "keyword_id": keyword_id,
                "is_kept": is_kept,
                "label": label if is_kept else "weak",
                "ai_reasoning": item.get("reason", ""),
                "extra_data": {
                    "reason_code": item.get("reason_code"),
                    "hook": meta.get("hook", item.get("hook")),
                    "scenario_note": meta.get("scenario_note", item.get("scenario_note")),
                    "engagement_score": score,
                },
                "transfer_channel": None,
                "is_fallback": False,
            })
        return results

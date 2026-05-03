"""
SEO Pre-Filter — SEO/GEO kanal filtreleme.

Kriterleri:
- İçerik derinliği: treasure (500-1000 kelime üretilebilir) vs shallow
- GEO uyum ön değerlendirmesi (AI snippet potansiyeli)
- H1/H2 başlık önerileri (downstream generation için extra_data'da)
"""
import json
import re
from typing import List, Dict, Optional

from app.core.channel.pre_filters.base_filter import BasePreFilter


class SeoPreFilter(BasePreFilter):
    """SEO/GEO kanalı için AI pre-filter."""

    CHANNEL = "SEO"
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
                    "label": "shallow",
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
        return f"""Sen bir SEO ön filtresisin. Her anahtar kelimeyi içerik derinliği açısından değerlendir.

KARAR KRİTERLERİ:

keep + treasure: Bu keyword ile 500-1000 kelimelik kaliteli, derinlikli bir içerik üretilebilir.
  Örnekler: "nasıl yapılır" rehberleri, karşılaştırma yazıları, teknik açıklamalar, alıcı rehberleri, "X nedir" konuları.
eliminate + shallow: İçerik derinliği yetersiz, sadece kısa tanım veya liste ile karşılanabilir.
  Örnekler: çok genel tek kelimelik terimler, marka adları, spesifik model numaraları, fiyat sorguları.
  - Marka/model odaklı sorgular için decision="eliminate", reason_code="BRAND_TERM" kullan.
  - Fiyat odaklı sorgular için decision="eliminate", reason_code="PRICE_TERM" kullan.

geo_suitable: Bu keyword için yerel/bölgesel SEO içeriği uygun mu? (true/false)

SADECE geçerli minified JSON döndür. Markdown, yorum veya açıklama YAZMA.
Schema:
{{"results":[{{"keyword_id":1,"decision":"keep","label":"treasure","reason_code":"HIGH_CONTENT_DEPTH","reason":"Rehber icerik uretilebilir","meta":{{"geo_suitable":true,"depth_label":"treasure"}}}},{{"keyword_id":2,"decision":"eliminate","label":"shallow","reason_code":"BRAND_TERM","reason":"Marka ve model odakli sorgu","meta":{{"depth_label":"shallow"}}}}]}}

ZORUNLU:
- Girdideki her keyword_id çıktıda tam 1 kez olmalı.
- keyword_id integer olmalı.
- reason alanı kısa ama anlamlı olmalı (max 10 kelime).

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
                    "label": item.get("depth_label", "treasure"),
                    "reason_code": item.get("reason_code", "HIGH_CONTENT_DEPTH"),
                    "reason": item.get("reason", ""),
                    "transfer_channel": None,
                    "meta": {
                        "suggested_h1": item.get("suggested_h1"),
                        "suggested_h2s": item.get("suggested_h2s", []),
                        "geo_suitable": item.get("geo_suitable", False),
                        "depth_label": item.get("depth_label", "treasure"),
                    },
                })
            for item in response.get("eliminated", []):
                if not isinstance(item, dict):
                    continue
                legacy_rows.append({
                    "keyword_id": item.get("keyword_id", item.get("id")),
                    "decision": "eliminate",
                    "label": item.get("depth_label", "shallow"),
                    "reason_code": item.get("reason_code", "LOW_CONTENT_DEPTH"),
                    "reason": item.get("reason", ""),
                    "transfer_channel": None,
                    "meta": {"depth_label": item.get("depth_label", "shallow")},
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
                elif item.get("depth_label") == "treasure":
                    decision = "keep"
                elif item.get("depth_label") == "shallow":
                    decision = "eliminate"
                elif item.get("suggested_h1"):
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
            depth_label = item.get("label") or meta.get("depth_label") or "shallow"
            if not meta:
                meta = {"depth_label": depth_label}
            results.append({
                "keyword_id": keyword_id,
                "is_kept": is_kept,
                "label": depth_label,
                "ai_reasoning": item.get("reason", ""),
                "extra_data": {"reason_code": item.get("reason_code"), **meta},
                "transfer_channel": None,
                "is_fallback": False,
            })
        return results

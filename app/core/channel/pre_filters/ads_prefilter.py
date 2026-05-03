"""
ADS Pre-Filter — Google Ads kanal filtreleme.

Kriterleri:
- Satın alma niyeti ve ticari değer filtresi
- "hot_sale" / "lead" etiketleme
- Elenen keyword'lerde SEO'ya aktarma önerisi
"""
import json
from typing import List, Dict

from app.core.channel.pre_filters.base_filter import BasePreFilter


class AdsPreFilter(BasePreFilter):
    """Google Ads kanalı için AI pre-filter."""

    CHANNEL = "ADS"

    def _build_filter_prompt(self, keywords: List[Dict]) -> str:
        keywords_json = json.dumps(
            [{"id": kw["id"], "keyword": kw["keyword"]} for kw in keywords],
            ensure_ascii=False
        )
        return f"""Sen bir Google Ads ön filtresisin. Her anahtar kelimeyi ADS kanalı için değerlendir.

KARAR KRİTERLERİ:

keep + hot_sale: Doğrudan satın alma niyeti güçlü. Fiyat sorgusu, belirli model/ürün arama, "satın al", "sipariş", "kargo", "taksit" gibi sinyaller içerir.
keep + lead: Satın alma araştırması. Genel kategori araması, marka karşılaştırma, "en iyi X", ürün inceleme. Dönüşüm potansiyeli var ama dolaylı.
eliminate: Aşağıdaki durumlarda elen:
  - İkinci el / kullanılmış: "2 el", "ikinci el", "sahibinden", "çıkma"
  - Ücretsiz / düşük değer: "bedava", "ücretsiz", "en ucuz", "ucuz"
  - Kiralama: "kiralık", "kiralik"
  - Bilgi amaçlı: "nedir", "nasıl yapılır", "pdf", "indir"
  - Navigational: sadece marka adı, site adı (doğrudan markaya yönelik)
  - Elenen keyword SEO'ya uygunsa transfer_channel="SEO" ve transfer_to_seo=true yaz.

SADECE geçerli minified JSON döndür. Markdown, yorum veya açıklama YAZMA.
Schema:
{{"results":[{{"keyword_id":1,"decision":"keep","label":"hot_sale","reason_code":"HIGH_BUYING_INTENT","reason":"Fiyat sorgusu, satin alma niyeti yuksek","transfer_channel":null}},{{"keyword_id":2,"decision":"eliminate","label":null,"reason_code":"NEGATIVE_TERM","reason":"Ikinci el sinyal iceriyor","transfer_channel":"SEO","meta":{{"transfer_to_seo":true}}}}]}}

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
        def _extract_keyword_id(item: Dict) -> int | None:
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
                    **item,
                    "decision": "keep",
                    "reason_code": item.get("reason_code", "HIGH_BUYING_INTENT"),
                    "transfer_channel": None,
                    "meta": {},
                })
            for item in response.get("eliminated", []):
                if not isinstance(item, dict):
                    continue
                transfer_to_seo = bool(item.get("transfer_to_seo"))
                legacy_rows.append({
                    **item,
                    "decision": "eliminate",
                    "reason_code": item.get("reason_code", "LOW_COMMERCIAL_VALUE"),
                    "transfer_channel": "SEO" if transfer_to_seo else None,
                    "meta": {"transfer_to_seo": transfer_to_seo},
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
                elif item.get("transfer_to_seo") or item.get("eliminated"):
                    decision = "eliminate"
                elif item.get("label"):
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
            transfer_channel = item.get("transfer_channel")
            if not transfer_channel and item.get("transfer_to_seo"):
                transfer_channel = "SEO"
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            if "transfer_to_seo" not in meta:
                meta["transfer_to_seo"] = transfer_channel == "SEO"
            results.append({
                "keyword_id": keyword_id,
                "is_kept": is_kept,
                "label": item.get("label") if is_kept else None,
                "ai_reasoning": item.get("reason", ""),
                "extra_data": {
                    "reason_code": item.get("reason_code"),
                    **meta,
                },
                "transfer_channel": transfer_channel if not is_kept else None,
                "is_fallback": False,
            })
        return results

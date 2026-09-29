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

    STAGE = "ads_prefilter"
    CHANNEL = "ADS"

    # Structured output (brand_filter/RSA kalıbı) — enum kısıtları prompt
    # metninde, doğrulama parse tarafında (eski SDK proto dönüşüm riski yok).
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword_id": {"type": "integer"},
                        "decision": {"type": "string"},          # keep|eliminate
                        "label": {"type": "string"},             # hot_sale|lead
                        "reason_code": {"type": "string"},
                        "reason": {"type": "string"},
                        "transfer_channel": {"type": "string"},
                    },
                    "required": ["keyword_id", "decision"],
                },
            }
        },
        "required": ["results"],
    }

    def _build_filter_prompt(self, keywords: List[Dict]) -> str:
        keywords_json = json.dumps(
            [{"id": kw["id"], "keyword": kw["keyword"]} for kw in keywords],
            ensure_ascii=False
        )
        product_definition = getattr(self, "_product_definition", None)
        product_block = ""
        if product_definition:
            product_block = f"""
MÜŞTERİ ÜRÜN TANIMI (lead kararında referans al):
{product_definition}
"""
        # v2.1 (Faz D / doc §4): hedef kitle + sınıf tanımlarının ürün-alanı
        # netleştirmesi YALNIZ v2_1 run'larda eklenir — v2 prompt'u bayt-aynı
        # kalır (D-harness referansı oynamaz).
        if getattr(self, "_algorithm_version", "v2") == "v2_1":
            audience = getattr(self, "_target_audience", None)
            if audience:
                product_block += f"""
HEDEF KİTLE:
{audience}
"""
            product_block += """
v2.1 SINIF NETLEŞTİRMESİ: eleme (-1) kararını ürün-ALANI belirler —
ürün-alanı DIŞI veya saf bilgi/jenerik sorgular elenir; ürün-alanı İÇİ
kategori/çözüm araması lead (1), satın alma/işlem eşiği hot_sale (2).
"""
        return f"""Sen bir Google Ads ön filtresisin. Her anahtar kelimeyi ADS kanalı için sınıflandır.
{product_block}
KARAR KRİTERLERİ (Skorlama v2 sınıfları):

keep + hot_sale (öncelikli sınıf): Arayan kişi satın alma/işlem kararının EŞİĞİNDE mi?
  Fiyat sorgusu, belirli model/ürün arama, "satın al", "sipariş", "kargo", "taksit" gibi sinyaller.
keep + lead (uygun sınıf): Aranan şey müşterinin ÜRÜN KATEGORİSİNDE bir çözüm mü?
  (Yukarıdaki müşteri ürün tanımını referans al.) Genel kategori araması, marka karşılaştırma,
  "en iyi X", ürün inceleme. Dönüşüm potansiyeli var ama dolaylı.
eliminate (elenir): Saf bilgi/haber/jenerik sorgu — yanlış kelimeye reklam doğrudan bütçe kaybıdır:
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
            # Skorlama v2 sınıf eşlemesi: hot_sale->2 (öncelikli),
            # lead->1 (uygun), eliminate->-1 (elendi).
            # Geçersiz/eksik label'lı keep SESSİZCE lead'e ÇEVRİLMEZ —
            # sonuçtan düşürülür, missing sayılır ve targeted retry'a gider;
            # çözülmezse karantina.
            if is_kept:
                label_norm = str(item.get("label") or "").lower()
                if label_norm == "hot_sale":
                    ai_class = 2
                elif label_norm == "lead":
                    ai_class = 1
                else:
                    continue  # missing → retry akışı
            else:
                ai_class = -1
            results.append({
                "keyword_id": keyword_id,
                "is_kept": is_kept,
                "label": item.get("label") if is_kept else None,
                "ai_class": ai_class,
                "ai_reasoning": item.get("reason", ""),
                "extra_data": {
                    "reason_code": item.get("reason_code"),
                    **meta,
                },
                "transfer_channel": transfer_channel if not is_kept else None,
                "is_fallback": False,
            })
        return results

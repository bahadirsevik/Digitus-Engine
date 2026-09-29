"""
SEO Pre-Filter — SEO/GEO kanal değerlendirmesi (Seçim Güvenilirliği v2).

SEÇİM AŞAMASI TAMAMEN DETERMİNİSTİKTİR — AI ÇAĞRISI YOKTUR:
- Fiyat/price/ücret filtresi `_pre_brand_defense_filter` hook'unda çalışır
  (brand-defense ayıklamasından ÖNCE → own-brand fiyat sorguları da
  yakalanır; PRICE_TERM hiçbir durumda bypass edilmez, bütçe sıfırken bile).
- Kalan tüm adaylar deterministik keep alır (`SEO_KEEP_DEFAULT`).
- Böylece AI/JSON hatası hiçbir SEO kelimesini SEÇİMDEN eleyemez
  (run-15'te 13/70 kelime parse hatasıyla karantinaya düşmüştü).

METADATA (depth_label, geo_suitable, h1/h2 önerileri) seçim SONRASI,
yalnızca kesin final SEO havuzu için `generate_metadata` ile üretilir ve
`is_kept`/`is_fallback`/`ai_class` alanlarına YAPISAL olarak dokunamaz.
Eleme yetkisi SEO'da yalnız: fiyat filtresi + marka dışlama (üst katman).
"""
import json
import re
from typing import Dict, List, Optional

from app.core.channel.pre_filters.base_filter import BasePreFilter
from app.core.telemetry.ai_cost_budget import BudgetError
from app.core.channel.brand_filter import BRAND_EXCLUDED_REASON
from app.core.constants import (
    TERMINAL_PREFILTER_REASONS,
    SEO_KEEP_DEFAULT_REASON,
    SEO_METADATA_BATCH_SIZE,
    SEO_METADATA_MAX_TOKENS,
    SEO_METADATA_REASON,
    SEO_METADATA_UNAVAILABLE_REASON,
)
from app.core.logging_config import get_task_logger
from app.database.models import Keyword, PreFilterResult

logger = get_task_logger()


class SeoPreFilter(BasePreFilter):
    """SEO kanalı: deterministik seçim + seçim-sonrası AI metadata."""

    STAGE = "seo_metadata"
    CHANNEL = "SEO"
    MAX_TOKENS = SEO_METADATA_MAX_TOKENS
    PRICE_ROOTS = ("fiyat", "price", "ucret")
    _TR_CHAR_MAP = str.maketrans({
        "ı": "i", "ş": "s", "ç": "c", "ö": "o", "ü": "u", "ğ": "g"
    })

    # Structured output — metadata şeması (seçimde kullanılmaz)
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword_id": {"type": "integer"},
                        "depth_label": {"type": "string"},
                        "reason": {"type": "string"},
                        "meta": {
                            "type": "object",
                            "properties": {
                                "geo_suitable": {"type": "boolean"},
                                "h1_suggestion": {"type": "string"},
                                "h2_suggestions": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                        },
                    },
                    "required": ["keyword_id", "depth_label"],
                },
            }
        },
        "required": ["results"],
    }

    # ═══════════════════════════════════════════════════════════
    # Deterministik seçim yolu (AI YOK)
    # ═══════════════════════════════════════════════════════════

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

    def _pre_brand_defense_filter(
        self, keywords: List[Dict]
    ) -> tuple[List[Dict], List[Dict]]:
        """Fiyat filtresi — brand-defense'tan ÖNCE, TEK uygulama noktası.

        Own-brand kelimeler de dahil tüm adaylara uygulanır; bloklu sonuçlar
        filter_candidates tarafından results'a eklenip persist edilir.
        """
        blocked = []
        remaining = []
        for kw in keywords:
            if self._is_price_term(str(kw.get("keyword", ""))):
                blocked.append({
                    "keyword_id": kw["id"],
                    "is_kept": False,
                    "label": "shallow",
                    "ai_class": None,
                    "ai_reasoning": "Deterministic price filter",
                    "extra_data": {"reason_code": "PRICE_TERM"},
                    "transfer_channel": None,
                    "is_fallback": False,
                })
            else:
                remaining.append(kw)
        return blocked, remaining

    def _batch_filter(self, keywords: List[Dict], progress_callback=None) -> List[Dict]:
        """Seçim aşaması: AI ÇAĞRISI YOK — deterministik keep.

        (Fiyat kontrolü _pre_brand_defense_filter'da zaten yapıldı; burada
        tekrar UYGULANMAZ — çift kayıt oluşmaz.)
        """
        results = [
            {
                "keyword_id": kw["id"],
                "is_kept": True,
                "label": None,  # metadata adımı doldurur (treasure/shallow)
                "ai_class": None,  # SEO'da sınıf ekseni yok
                "ai_reasoning": (
                    "SEO seçim aşaması deterministik keep "
                    "(metadata seçim sonrası üretilir)"
                ),
                "extra_data": {"reason_code": SEO_KEEP_DEFAULT_REASON},
                "transfer_channel": None,
                "is_fallback": False,
            }
            for kw in keywords
        ]
        if progress_callback:
            progress_callback(self.CHANNEL, 1)
        return results

    # ═══════════════════════════════════════════════════════════
    # Seçim-SONRASI metadata üretimi (yalnız final SEO havuzu)
    # ═══════════════════════════════════════════════════════════

    def generate_metadata(
        self,
        scoring_run_id: int,
        keyword_ids: List[int],
        progress_callback=None,
    ) -> Dict[str, int]:
        """Kesin final SEO havuzu için içerik metadata'sı üretir.

        Genel karantina zincirini KULLANMAZ: başarısızlık yalnızca
        SEO_METADATA_UNAVAILABLE işareti bırakır; is_kept / final_rank /
        adjusted_score ASLA değişmez. Metadata çağrıları expansion
        bütçesinin DIŞINDADIR (seçim bittikten sonra koşar).
        """
        self._ai_calls_used = 0
        self._single_retries_used = 0
        self._budget = None  # metadata expansion bütçesine dahil değil

        if not keyword_ids:
            return {"requested": 0, "updated": 0, "unavailable": 0,
                    "ai_calls_used": 0}

        rows = (
            self.db.query(Keyword)
            .filter(Keyword.id.in_(keyword_ids))
            .all()
        )
        keywords = [{"id": kw.id, "keyword": kw.keyword} for kw in rows]

        parsed_all: List[Dict] = []
        for i in range(0, len(keywords), SEO_METADATA_BATCH_SIZE):
            batch = keywords[i:i + SEO_METADATA_BATCH_SIZE]
            parsed_all.extend(self._process_metadata_batch(batch))
            if progress_callback:
                progress_callback(self.CHANNEL, 1)

        resolved_ids = {r["keyword_id"] for r in parsed_all}
        unavailable_ids = [
            kw["id"] for kw in keywords if kw["id"] not in resolved_ids
        ]

        updated = self._save_metadata_results(
            scoring_run_id, parsed_all, unavailable_ids
        )
        summary = {
            "requested": len(keywords),
            "updated": updated,
            "unavailable": len(unavailable_ids),
            "ai_calls_used": self._ai_calls_used,
        }
        logger.info(f"SEO metadata tamamlandı: {summary}")
        return summary

    def _process_metadata_batch(self, batch: List[Dict]) -> List[Dict]:
        """Metadata batch işleme — karantinasız.

        1 parse retry + eksik-alt-küme kurtarması (_retry_missing_keywords
        fallback ÜRETMEZ, güvenle yeniden kullanılır). Çözülmeyenler
        çağırana 'eksik' olarak döner (unavailable işaretlenir).
        """
        from app.generators.ai_service import logical_request, mark_attempt_failed

        batch_ids = {kw["id"] for kw in batch}
        parsed: List[Dict] = []

        # Aynı-prompt retry zinciri = tek mantıksal çağrı (Codex v8-4):
        # sabit request_id + artan attempt telemetriye yazılır
        with logical_request(self.ai_service):
            for attempt in range(2):  # ilk çağrı + 1 retry
                try:
                    raw = self._complete_json(self._build_filter_prompt(batch))
                    parsed_json = self._safe_parse_json(raw)
                    results = self._parse_ai_response(parsed_json, batch)
                    parsed = [r for r in results if r.get("keyword_id") in batch_ids]
                    break
                except BudgetError:
                    # Metadata SECIM SONRASI kosar; butce dolduysa retry
                    # anlamsizdir — hizlica yukari birak, motor sebebi
                    # BUDGET_EXCEEDED olarak etiketler (secim degismez).
                    raise
                except Exception as e:
                    mark_attempt_failed(self.ai_service, f"parse_error: {e}")
                    logger.warning(
                        f"SEO metadata batch hata (deneme {attempt + 1}/2): {e}"
                    )

            missing_ids = batch_ids - {r["keyword_id"] for r in parsed}
            if missing_ids:
                mark_attempt_failed(
                    self.ai_service,
                    f"missing_results:{len(missing_ids)}/{len(batch)}",
                )

        if missing_ids:
            missing = [kw for kw in batch if kw["id"] in missing_ids]
            # _retry_missing_keywords fallback üretmez — yalnız kurtarılanları
            # döndürür; kurtarılamayanlar unavailable olur (karantina DEĞİL)
            with logical_request(self.ai_service):
                parsed.extend(self._retry_missing_keywords(missing))

        return parsed

    def _make_metadata_unavailable(self, existing: PreFilterResult) -> None:
        """Yalnız reason_code işaretler — SEÇİM ALANLARINA DOKUNMAZ."""
        merged = dict(existing.extra_data or {})
        merged["reason_code"] = SEO_METADATA_UNAVAILABLE_REASON
        existing.extra_data = merged

    def _save_metadata_results(
        self,
        scoring_run_id: int,
        parsed_results: List[Dict],
        unavailable_ids: List[int],
    ) -> int:
        """Metadata'yı MEVCUT PreFilterResult satırlarına merge eder.

        Yapısal garanti: yalnız label + ai_reasoning + metadata extra_data
        güncellenir; is_kept / is_fallback / ai_class / transfer_channel
        HİÇBİR durumda değişmez (_save_results KULLANILMAZ — onun UPDATE
        dalı is_kept'i ezer).
        """
        updated = 0
        for r in parsed_results:
            existing = self._get_seo_row(scoring_run_id, r["keyword_id"])
            if existing is None:
                logger.warning(
                    f"SEO metadata: PreFilterResult yok, atlanıyor "
                    f"(kw={r['keyword_id']})"
                )
                continue
            if (existing.extra_data or {}).get("reason_code") == BRAND_EXCLUDED_REASON:
                continue  # marka dışlama kaydı korunur

            extra = r.get("extra_data") or {}
            merged = dict(existing.extra_data or {})
            merged.update({
                "reason_code": SEO_METADATA_REASON,
                "depth_label": extra.get("depth_label"),
                "geo_suitable": extra.get("geo_suitable", False),
                "h1_suggestion": extra.get("h1_suggestion"),
                "h2_suggestions": extra.get("h2_suggestions") or [],
            })
            existing.label = r.get("label")
            existing.ai_reasoning = r.get("ai_reasoning", "")
            existing.extra_data = merged
            updated += 1

        for keyword_id in unavailable_ids:
            existing = self._get_seo_row(scoring_run_id, keyword_id)
            if existing is None:
                continue
            reason = (existing.extra_data or {}).get("reason_code")
            # Başarılı metadata (SEO_METADATA) sonradan gelen toplu hatayla
            # EZİLMEZ; fiyat/marka elemesi kayıtları da işaretlenmez.
            if reason == SEO_METADATA_REASON or reason in TERMINAL_PREFILTER_REASONS:
                continue
            self._make_metadata_unavailable(existing)

        self.db.commit()
        return updated

    def _get_seo_row(self, scoring_run_id: int, keyword_id: int):
        return (
            self.db.query(PreFilterResult)
            .filter(
                PreFilterResult.scoring_run_id == scoring_run_id,
                PreFilterResult.keyword_id == keyword_id,
                PreFilterResult.channel == self.CHANNEL,
            )
            .first()
        )

    # ═══════════════════════════════════════════════════════════
    # Metadata prompt + parse (seçimde çağrılmaz)
    # ═══════════════════════════════════════════════════════════

    def _build_filter_prompt(self, keywords: List[Dict]) -> str:
        keywords_json = json.dumps(
            [{"id": kw["id"], "keyword": kw["keyword"]} for kw in keywords],
            ensure_ascii=False
        )
        return f"""Sen bir SEO içerik değerlendiricisisin. Her anahtar kelime için içerik METADATA'sı üret.
Sen ELEME KARARI VERMEZSİN — her kelime SEO adayı olarak kalır; sen yalnızca sinyal üretirsin.

DEĞERLENDİRME ALANLARI:

depth_label:
- "treasure": 500-1000 kelimelik kaliteli, derinlikli içerik üretilebilir
  (rehberler, karşılaştırmalar, teknik açıklamalar, alıcı rehberleri, "X nedir" konuları)
- "shallow": İçerik derinliği sınırlı; kısa tanım veya listeyle karşılanır
  (çok genel tek kelimelik terimler, marka/model odaklı sorgular)

geo_suitable: Bu keyword için yerel/bölgesel SEO içeriği uygun mu? (true/false)
h1_suggestion: İçerik için H1 başlık önerisi (string)
h2_suggestions: 2-4 adet H2 alt başlık önerisi (string listesi)

SADECE geçerli minified JSON döndür. Markdown, yorum veya açıklama YAZMA.
Schema:
{{"results":[{{"keyword_id":1,"depth_label":"treasure","reason":"Rehber icerik uretilebilir","meta":{{"geo_suitable":true,"h1_suggestion":"X Nedir ve Nasil Kullanilir?","h2_suggestions":["X'in Avantajlari","X Secerken Dikkat Edilecekler"]}}}}]}}

ZORUNLU:
- Girdideki her keyword_id çıktıda tam 1 kez olmalı.
- keyword_id integer olmalı.
- reason alanı kısa ama anlamlı olmalı (max 10 kelime).
- depth_label yalnızca "treasure" veya "shallow" olmalı.

keywords={keywords_json}
"""

    def _parse_ai_response(
        self, response, original: List[Dict]
    ) -> List[Dict]:
        """AI metadata yanıtını standart formata çevirir (eleme kararı YOK)."""
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

        if not isinstance(response, list):
            return []

        results = []
        for item in response:
            if not isinstance(item, dict):
                continue
            keyword_id = _extract_keyword_id(item)
            if keyword_id is None:
                continue

            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            depth_label = (
                item.get("depth_label")
                or meta.get("depth_label")
                or item.get("label")
                or "treasure"
            )
            if depth_label not in ("treasure", "shallow"):
                depth_label = "treasure"

            h2_raw = meta.get("h2_suggestions", item.get("h2_suggestions"))
            h2_suggestions = [
                str(h) for h in h2_raw if isinstance(h, str)
            ] if isinstance(h2_raw, list) else []

            results.append({
                "keyword_id": keyword_id,
                # Metadata sonucu — is_kept _save_metadata_results tarafından
                # zaten YOK SAYILIR (yapısal garanti); True bilgilendiricidir
                "is_kept": True,
                "label": depth_label,
                "ai_class": None,
                "ai_reasoning": item.get("reason", ""),
                # Key'ler PreFilterEnricher ile hizalı: h1_suggestion/h2_suggestions
                "extra_data": {
                    "reason_code": SEO_METADATA_REASON,
                    "depth_label": depth_label,
                    "geo_suitable": bool(meta.get("geo_suitable", item.get("geo_suitable", False))),
                    "h1_suggestion": meta.get("h1_suggestion", item.get("h1_suggestion")),
                    "h2_suggestions": h2_suggestions,
                },
                "transfer_channel": None,
                "is_fallback": False,
            })
        return results

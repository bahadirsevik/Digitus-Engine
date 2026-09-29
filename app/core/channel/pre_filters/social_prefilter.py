"""
Social Pre-Filter — Sosyal medya kanal filtreleme (Skorlama v2).

Kriterleri (doküman Bölüm 6.2 — konuşulabilirlik sınıfı):
- 3 binary boyut: opinion_discussion (görüş/tartışma değeri),
  curiosity_comparison (merak/kıyas kalıbı), agenda_theme (gündem teması)
- ai_class = boyutların toplamı (0-3)
- Eleme kararını AI DEĞİL kod verir: ai_class == 0 VE momentum (MB) <= 0.3
  olan kelime elenir (MB, KeywordScore.metrics_snapshot.derived'den okunur)
- hook + scenario_note üretimi ŞART — downstream social generators kullanır
- Label eşleme: 3 -> viral, 1-2 -> moderate, 0 -> weak
"""
import json
import re
from typing import Dict, List, Optional

from app.core.channel.pre_filters.base_filter import BasePreFilter
from app.core.constants import SOCIAL_MOMENTUM_THRESHOLD
from app.database.models import KeywordScore


class MomentumSnapshotMissingError(RuntimeError):
    """v2.1: derived.t3 eksik/geçersiz — SOCIAL eleme kuralı hesaplanamaz;
    assignment final yazımdan önce fail eder (plan Faz D)."""


class SocialPreFilter(BasePreFilter):
    """Sosyal medya kanalı için AI pre-filter."""

    STAGE = "social_prefilter"
    CHANNEL = "SOCIAL"

    # Structured output — üç binary boyut + içerik malzemesi
    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword_id": {"type": "integer"},
                        "dims": {
                            "type": "object",
                            "properties": {
                                "opinion_discussion": {"type": "integer"},
                                "curiosity_comparison": {"type": "integer"},
                                "agenda_theme": {"type": "integer"},
                            },
                            "required": [
                                "opinion_discussion",
                                "curiosity_comparison",
                                "agenda_theme",
                            ],
                        },
                        "reason": {"type": "string"},
                        "meta": {
                            "type": "object",
                            "properties": {
                                "hook": {"type": "string"},
                                "scenario_note": {"type": "string"},
                            },
                        },
                    },
                    "required": ["keyword_id", "dims"],
                },
            }
        },
        "required": ["results"],
    }
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

    def filter_candidates(
        self,
        scoring_run_id: int,
        keyword_ids: Optional[List[int]] = None,
        progress_callback=None,
        budget=None,
    ) -> Dict:
        # Momentum haritası: eleme kuralı (sınıf 0 + momentum <= eşik).
        # v2: MB (liste-göreli persentil, mevcut davranış AYNEN).
        # v2.1 (Faz D / doc §6.1): M = min(max(derived.t3, 0), 1) — mutlak
        # momentum; canonical kaynak metrics_snapshot.derived.t3.
        from app.database.models import ScoringRun as _ScoringRun

        run = self.db.query(_ScoringRun).filter(
            _ScoringRun.id == scoring_run_id
        ).first()
        algo = getattr(run, "algorithm_version", "v2") or "v2" if run else "v2"
        # Codex Faz D #2: hangi metriğin KULLANILDIĞI extra_data'da ayrık
        # anahtar + momentum_metric ile saklanır; v2_1'de MB de replay için
        # birlikte taşınır (Faz F M/MB offline karşılaştırması yanılmasın)
        if algo == "v2_1":
            self._mb_map = self._load_m_map_v21(scoring_run_id)
            self._momentum_metric = "m"
            # Codex 3. tur #2: audit MB strict okunur — eksik/geçersiz MB
            # sahte 0.0 yerine None kalır (mb_available=False ile raporlanır;
            # M/MB replay'i sahte sıfırla yanılmaz)
            self._mb_audit_map = self._load_mb_audit_map_v21(scoring_run_id)
        else:
            self._mb_map = self._load_mb_map(scoring_run_id)
            self._momentum_metric = "mb"
            self._mb_audit_map = None
        return super().filter_candidates(
            scoring_run_id,
            keyword_ids=keyword_ids,
            progress_callback=progress_callback,
            budget=budget,
        )

    def _load_mb_map(self, scoring_run_id: int) -> Dict[int, float]:
        """KeywordScore.metrics_snapshot.derived.mb -> keyword_id haritası.

        Eski run'larda (v2 öncesi snapshot) 'derived' yoktur -> 0.0 (guard).
        """
        rows = (
            self.db.query(KeywordScore.keyword_id, KeywordScore.metrics_snapshot)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )
        mb_map: Dict[int, float] = {}
        for keyword_id, snapshot in rows:
            derived = (snapshot or {}).get("derived") or {}
            try:
                mb_map[keyword_id] = float(derived.get("mb") or 0.0)
            except (TypeError, ValueError):
                mb_map[keyword_id] = 0.0
        return mb_map

    @staticmethod
    def _valid_audit_mb(mb) -> Optional[float]:
        """v2.1 audit MB geçerlilik kuralı (Codex 4. tur #2): MB persentil
        tanımı gereği [0,1] aralığında sonlu bir sayıdır — None/bool/parse
        edilemeyen/NaN/Infinity/aralık dışı değerler GEÇERSİZ (None)."""
        import math

        if mb is None or isinstance(mb, bool):
            return None
        try:
            value = float(mb)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or not (0.0 <= value <= 1.0):
            return None
        return value

    def _load_mb_audit_map_v21(self, scoring_run_id: int):
        """v2.1 replay-audit MB haritası — STRICT: eksik/geçersiz mb None
        (sahte 0.0 YAZILMAZ; Codex 3. tur #2, geçerlilik: _valid_audit_mb)."""
        rows = (
            self.db.query(KeywordScore.keyword_id, KeywordScore.metrics_snapshot)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )
        audit: Dict[int, Optional[float]] = {}
        for keyword_id, snapshot in rows:
            derived = (snapshot or {}).get("derived") or {}
            audit[keyword_id] = self._valid_audit_mb(derived.get("mb"))
        return audit

    def _load_m_map_v21(self, scoring_run_id: int) -> Dict[int, float]:
        """v2.1 canonical M haritası: min(max(derived.t3, 0), 1).

        derived.t3 SIFIR olabilir (.get + is-None ayrımı — 0 korunur);
        alan EKSİK/geçersizse sessizce 0 KABUL EDİLMEZ — assignment final
        yazımdan önce MOMENTUM_SNAPSHOT_MISSING ile fail eder (plan Faz D).
        Üst düzey ham-yüzde trend_3m veya derived.mb yanlışlıkla kullanılmaz.
        """
        rows = (
            self.db.query(KeywordScore.keyword_id, KeywordScore.metrics_snapshot)
            .filter(KeywordScore.scoring_run_id == scoring_run_id)
            .all()
        )
        m_map: Dict[int, float] = {}
        missing: List[int] = []
        for keyword_id, snapshot in rows:
            derived = (snapshot or {}).get("derived") or {}
            t3 = derived.get("t3")
            if t3 is None:
                missing.append(keyword_id)
                continue
            try:
                m_map[keyword_id] = min(max(float(t3), 0.0), 1.0)
            except (TypeError, ValueError):
                missing.append(keyword_id)
        if missing:
            raise MomentumSnapshotMissingError(
                f"MOMENTUM_SNAPSHOT_MISSING: {len(missing)} kelimede "
                f"derived.t3 yok/geçersiz (örnek keyword_id: {missing[:5]}) — "
                f"v2_1 SOCIAL eleme kuralı hesaplanamaz, atama durduruldu."
            )
        return m_map

    def _batch_filter(self, keywords: List[Dict], progress_callback=None) -> List[Dict]:
        blocked = []
        to_ai = []
        for kw in keywords:
            keyword_text = str(kw.get("keyword", ""))
            if self._is_price_term(keyword_text):
                blocked.append({
                    "keyword_id": kw["id"],
                    "is_kept": False,
                    "label": "weak",
                    "ai_class": None,
                    "ai_reasoning": "Deterministic price filter",
                    "extra_data": {"reason_code": "PRICE_TERM"},
                    "transfer_channel": None,
                    "is_fallback": False,
                })
            else:
                to_ai.append(kw)

        if to_ai:
            ai_results = super()._batch_filter(to_ai, progress_callback=progress_callback)
        else:
            ai_results = []
            if progress_callback:
                progress_callback(self.CHANNEL, 1)
        return blocked + ai_results

    def _build_filter_prompt(self, keywords: List[Dict]) -> str:
        keywords_json = json.dumps(
            [{"id": kw["id"], "keyword": kw["keyword"]} for kw in keywords],
            ensure_ascii=False
        )
        # v2.1 (Faz D / doc §6.1): ürün tanımı + sosyal strateji modu prompt'a
        # girer — YALNIZ v2_1 run'larda (v2 prompt'u bayt-aynı kalır).
        v21_block = ""
        if getattr(self, "_algorithm_version", "v2") == "v2_1":
            product_definition = getattr(self, "_product_definition", None)
            mode = getattr(self, "_social_mode", None) or "hype"
            if product_definition:
                v21_block += f"""
MÜŞTERİ ÜRÜN TANIMI (konu-ürün bağını değerlendirirken referans al):
{product_definition}
"""
            v21_block += f"""
SOSYAL STRATEJİ MODU: {mode} — {'gündem/ivme kovalayan içerik' if mode == 'hype' else 'topluluk/editoryal içerik'}
"""
        return f"""Sen bir sosyal medya konuşulabilirlik değerlendiricisisin.{v21_block} Her anahtar kelimeyi ÜÇ BİNARY BOYUTTA (0 veya 1) puanla:

1. opinion_discussion: Görüş/tartışma değeri — insanlar bu konuda fikir beyan eder mi, tartışma çıkar mı?
2. curiosity_comparison: Merak/kıyas kalıbı — "en iyi", "en çok", "geleceğin...", karşılaştırma potansiyeli var mı?
3. agenda_theme: Gündem teması — kendiliğinden ilgi taşıyan kültürel/güncel bir tema mı?

Sen ELEME KARARI VERMEZSİN; sadece boyutları puanlar ve içerik malzemesi üretirsin.

Her kelime için ayrıca:
- hook: Güçlü bir kanca cümlesi (kısa video açılışı)
- scenario_note: Kısa video senaryo notu (1 cümle)
- reason: Kısa gerekçe (max 10 kelime)

SADECE geçerli minified JSON döndür. Markdown, yorum veya açıklama YAZMA.
Schema:
{{"results":[{{"keyword_id":1,"dims":{{"opinion_discussion":1,"curiosity_comparison":1,"agenda_theme":0}},"reason":"Karsilastirma icerigi, tartisma yaratir","meta":{{"hook":"Bu urun gercekten degdi mi?","scenario_note":"Fiyat-performans karsilastirma"}}}}]}}

ZORUNLU:
- Girdideki her keyword_id çıktıda tam 1 kez olmalı.
- keyword_id integer olmalı.
- dims içindeki üç alan da 0 veya 1 (integer) olmalı.
- hook ve scenario_note alanlarını her kelime için doldur.

keywords={keywords_json}
"""

    @staticmethod
    def _coerce_dim(value) -> int:
        """Boyut değerini 0/1'e zorlar; parse edilemezse 0."""
        if isinstance(value, bool):
            return int(value)
        try:
            return 1 if int(value) > 0 else 0
        except (TypeError, ValueError):
            return 0

    def _parse_ai_response(
        self, response, original: List[Dict]
    ) -> List[Dict]:
        """AI yanıtını standart formata çevirir; is_kept kararını KOD verir."""
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

        mb_map = getattr(self, "_mb_map", {}) or {}
        results = []
        for item in response:
            if not isinstance(item, dict):
                continue
            keyword_id = _extract_keyword_id(item)
            if keyword_id is None:
                continue

            # Boyutlar: dims objesi veya düz alanlar (AI şema sapması toleransı)
            dims_raw = item.get("dims") if isinstance(item.get("dims"), dict) else item
            dims = {
                "opinion_discussion": self._coerce_dim(dims_raw.get("opinion_discussion")),
                "curiosity_comparison": self._coerce_dim(dims_raw.get("curiosity_comparison")),
                "agenda_theme": self._coerce_dim(dims_raw.get("agenda_theme")),
            }
            ai_class = sum(dims.values())

            # Eleme kuralı (doc §6.1): üç boyut da 0 VE momentum <= eşik.
            # Kararı AI değil kod verir. Momentum metriği v2'de MB (persentil),
            # v2_1'de canonical M (mutlak, derived.t3) — _mb_map ona göre dolu.
            momentum = mb_map.get(keyword_id, 0.0)
            is_kept = ai_class > 0 or momentum > SOCIAL_MOMENTUM_THRESHOLD

            # Label: sınıf eşlemesi (downstream tüketiciler viral/moderate/weak bekler)
            if ai_class >= 3:
                label = "viral"
            elif ai_class >= 1:
                label = "moderate"
            else:
                label = "weak"

            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            if is_kept:
                reason_code = "TALKABILITY_CLASS_" + str(ai_class)
                if ai_class == 0:
                    # sınıf 0 ama momentum > eşik (v2: MB, v2_1: M)
                    reason_code = "MOMENTUM_RESCUE"
            else:
                reason_code = "LOW_TALKABILITY_LOW_MOMENTUM"

            results.append({
                "keyword_id": keyword_id,
                "is_kept": is_kept,
                "label": label if is_kept else "weak",
                "ai_class": ai_class,
                "ai_reasoning": item.get("reason", ""),
                # Codex Faz D #2: kullanılan metrik AYRIK anahtarla saklanır —
                # v2: {"mb": ..., momentum_metric: "mb"}; v2_1: {"m": ...,
                # momentum_metric: "m"} + replay için MB de birlikte taşınır
                "extra_data": {
                    "reason_code": reason_code,
                    "dims": dims,
                    "momentum_metric": getattr(self, "_momentum_metric", "mb"),
                    getattr(self, "_momentum_metric", "mb"): momentum,
                    **(
                        # Replay MB'si strict: eksikse null + mb_available
                        # bayrağı (sahte 0.0 YOK — Codex 3. tur #2)
                        (lambda _mb: {"mb": _mb,
                                      "mb_available": _mb is not None})(
                            (getattr(self, "_mb_audit_map", None) or {}).get(
                                keyword_id)
                        )
                        if getattr(self, "_momentum_metric", "mb") == "m"
                        else {}
                    ),
                    "hook": meta.get("hook", item.get("hook")),
                    "scenario_note": meta.get("scenario_note", item.get("scenario_note")),
                },
                "transfer_channel": None,
                "is_fallback": False,
            })
        return results

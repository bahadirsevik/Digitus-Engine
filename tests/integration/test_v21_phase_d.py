# -*- coding: utf-8 -*-
"""v2.1 Faz D — AI sözleşmeleri testleri (plan §7).

Kapsam: SEO strategy_fit (v2_1 prompt/şema/INSERT+UPDATE/NULL fallback),
ADS tek-kapı (v2_1'de genel intent elemez; hard-negative kalır), SOCIAL
v2_1 M-kaynağı (MOMENTUM_SNAPSHOT_MISSING), v2 prompt'larının BAYT-AYNI
kaldığı (D-harness referansı)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.channel.intent_analyzer import IntentAnalyzer
from app.database.models import (
    ChannelCandidate,
    IntentAnalysis,
    KeywordScore,
)

APPROVED_STRATEGY = {
    "product_definition": "Beyaz saçları eski rengine döndüren kozmetik ürün.",
    "content_strategy": "Ürün-problem alanının tamamı.",
    "social_mode": "hype",
    "schema_version": 1,
    "status": "approved",
}


def _v21_manifest(audience=None):
    """Dispatch-anı manifest snapshot'ı — v2_1 prompt bağlamının TEK kaynağı
    (Codex Faz D #1: canlı BrandProfile OKUNMAZ)."""
    return {"strategy_snapshot": {
        "product_definition": APPROVED_STRATEGY["product_definition"],
        "content_strategy": APPROVED_STRATEGY["content_strategy"],
        "social_mode": APPROVED_STRATEGY["social_mode"],
        "schema_version": 1,
        "fingerprint": "test-fp",
        "strategy_version": 1,
        "target_audience": audience,
    }}


class _CannedIntentAI:
    """Sabit yanıtlı intent AI'ı — istekteki keyword_id'lere cevap üretir."""

    def __init__(self, item_builder):
        self.item_builder = item_builder
        self.prompts = []

    def complete_json(self, prompt, **kwargs):
        import re

        self.prompts.append(prompt)
        # Yalnız keyword listesi satırları: "- <id>: <kelime>" (taksonomi
        # satırları "- transactional: ..." biçiminde — sayısal id şart)
        ids = [int(m) for m in re.findall(r"^- (\d+): ", prompt, re.MULTILINE)]
        return json.dumps({"results": [self.item_builder(i) for i in ids]})


def _mk_candidate(db, run, ws, make_keyword, text, channel):
    kw = make_keyword(text, brand_profile_id=ws.id)
    db.add(ChannelCandidate(
        scoring_run_id=run.id, keyword_id=kw.id, channel=channel,
        raw_score=10, rank_in_channel=1,
    ))
    db.commit()
    return kw


def _analyzer(db, ai):
    analyzer = IntentAnalyzer(db, ai)
    return analyzer


def test_v21_seo_prompt_and_strategy_fit_persistence(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """v2_1 SEO: prompt beyan bloğu + strategy_fit sorusu taşır (gt sorusu
    YOK); strategy_fit INSERT ve UPDATE dallarında persist edilir; gt NULL."""
    ws = make_workspace("v21 seo")
    # Canlı strateji BİLEREK FARKLI/ilgisiz — prompt bağlamı manifest'ten
    # gelmeli (Codex Faz D #1: değişmezlik kanıtı)
    ws.channel_strategy = {**APPROVED_STRATEGY,
                           "content_strategy": "CANLI DEĞİŞMİŞ BEYAN"}
    ws.strategy_version = 9
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    run.algorithm_version = "v2_1"
    run.execution_manifest = _v21_manifest()
    kw = _mk_candidate(db_session, run, ws, make_keyword,
                       "beyaz saç çözümü", "SEO")

    ai = _CannedIntentAI(lambda i: {
        "keyword_id": i, "intent_type": "informational", "confidence": 0.9,
        "strategy_fit": 1, "ga": 1, "reasoning": "beyanla uyumlu",
    })
    result = _analyzer(db_session, ai).analyze_candidates(run.id, "SEO")
    assert result["analyzed"] == 1

    prompt = ai.prompts[0]
    assert "İÇERİK STRATEJİSİ BEYANI" in prompt
    # Değişmezlik: bağlam MANIFEST'ten — canlı profildeki değişmiş beyan DEĞİL
    assert "Ürün-problem alanının tamamı." in prompt
    assert "CANLI DEĞİŞMİŞ BEYAN" not in prompt
    assert "strategy_fit (0 veya 1)" in prompt
    assert "gt (0 veya 1)" not in prompt  # v2_1'de gt SORULMAZ
    # v2.1'de ürün tanımı onaylı BEYAN'dan (manifest) gelir
    assert "kozmetik ürün" in prompt

    row = db_session.query(IntentAnalysis).filter_by(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SEO"
    ).one()
    assert row.strategy_fit is True
    assert row.gt is None  # kolon anlamı değişmedi — v2_1'de NULL
    assert row.ga is True

    # UPDATE dalı: aynı adayı yeniden analiz et (fit 0'a düşsün)
    ai2 = _CannedIntentAI(lambda i: {
        "keyword_id": i, "intent_type": "informational", "confidence": 0.9,
        "strategy_fit": 0, "ga": 0, "reasoning": "uyumsuz",
    })
    _analyzer(db_session, ai2).analyze_candidates(run.id, "SEO")
    db_session.expire_all()
    row = db_session.query(IntentAnalysis).filter_by(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SEO"
    ).one()
    assert row.strategy_fit is False


def test_v2_seo_prompt_unchanged(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """v2 SEO prompt'u Faz D sonrası DEĞİŞMEDİ: gt sorusu var,
    strategy_fit/beyan bloğu YOK (D-harness referansı)."""
    ws = make_workspace("v2 seo")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    _mk_candidate(db_session, run, ws, make_keyword, "hisse analizi", "SEO")

    ai = _CannedIntentAI(lambda i: {
        "keyword_id": i, "intent_type": "informational", "confidence": 0.9,
        "gt": 0, "ga": 1, "reasoning": "x",
    })
    _analyzer(db_session, ai).analyze_candidates(run.id, "SEO")
    prompt = ai.prompts[0]
    assert "gt (0 veya 1)" in prompt
    assert "strategy_fit" not in prompt
    assert "İÇERİK STRATEJİSİ BEYANI" not in prompt


def test_v21_ads_intent_has_no_elimination_authority(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """v2_1 ADS: informational + düşük güven bile GEÇER (audit kaydıyla);
    deterministik hard-negative yine eler. v2 davranışı değişmez."""
    ws = make_workspace("v21 ads")
    v21 = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    v21.algorithm_version = "v2_1"
    v21.execution_manifest = _v21_manifest()
    kw_info = _mk_candidate(db_session, v21, ws, make_keyword,
                            "saç bakımı nedir", "ADS")
    kw_hard = _mk_candidate(db_session, v21, ws, make_keyword,
                            "en ucuz saç boyası", "ADS")

    ai = _CannedIntentAI(lambda i: {
        "keyword_id": i, "intent_type": "informational", "confidence": 0.2,
        "reasoning": "bilgi arayışı",
    })
    _analyzer(db_session, ai).analyze_candidates(v21.id, "ADS")

    rows = {
        r.keyword_id: r
        for r in db_session.query(IntentAnalysis).filter_by(
            scoring_run_id=v21.id, channel="ADS"
        )
    }
    assert rows[kw_info.id].is_passed is True   # intent ELEYEMEZ (v2_1)
    assert rows[kw_info.id].intent_type == "informational"  # audit kaydı
    assert rows[kw_hard.id].is_passed is False  # hard-negative KALIR

    # v2 kontrolü: aynı cevap v2 run'da elenir (davranış değişmedi)
    v2 = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    kw_v2 = _mk_candidate(db_session, v2, ws, make_keyword,
                          "saç bakımı hakkında", "ADS")
    _analyzer(db_session, _CannedIntentAI(lambda i: {
        "keyword_id": i, "intent_type": "informational", "confidence": 0.2,
        "reasoning": "bilgi",
    })).analyze_candidates(v2.id, "ADS")
    v2_row = db_session.query(IntentAnalysis).filter_by(
        scoring_run_id=v2.id, keyword_id=kw_v2.id, channel="ADS"
    ).one()
    assert v2_row.is_passed is False


def test_v21_missing_snapshot_fails_closed_before_ai(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Codex 3. tur #1 (FAIL-CLOSED): v2_1'de manifest snapshot'ı yoksa —
    canlı profil DOLU olsa bile — SIFIR AI çağrısıyla tipli hata; canlı
    profile sessizce düşülmez. Eksik-alanlı snapshot da aynı."""
    from app.core.policy.channel_strategy import StrategySnapshotMissingError

    ws = make_workspace("v21 fail-closed", profile_data={
        "brand_summary": "Canlı profil DOLU", "products": ["ürün"],
        "target_audience": "herkes",
    })
    ws.channel_strategy = dict(APPROVED_STRATEGY)  # canlı onay bile VAR
    ws.strategy_version = 1
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    run.algorithm_version = "v2_1"  # manifest YOK
    kw = _mk_candidate(db_session, run, ws, make_keyword,
                       "fail closed kelime", "SEO")

    ai = _CannedIntentAI(lambda i: {"keyword_id": i,
                                    "intent_type": "informational",
                                    "confidence": 0.9, "strategy_fit": 1,
                                    "ga": 1, "reasoning": "x"})
    with pytest.raises(StrategySnapshotMissingError,
                       match="STRATEGY_SNAPSHOT_MISSING"):
        _analyzer(db_session, ai).analyze_candidates(run.id, "SEO")
    assert ai.prompts == []  # SIFIR AI çağrısı
    assert db_session.query(IntentAnalysis).filter_by(
        scoring_run_id=run.id, keyword_id=kw.id).count() == 0

    # Eksik alanlı snapshot da fail-closed
    run.execution_manifest = {"strategy_snapshot": {
        "product_definition": "var", "content_strategy": "",
        "social_mode": "hype",
    }}
    db_session.commit()
    with pytest.raises(StrategySnapshotMissingError, match="eksik"):
        _analyzer(db_session, ai).analyze_candidates(run.id, "SEO")
    assert ai.prompts == []

    # Prefilter yolu da aynı sözleşmeye tabi (base_filter) — geçmiş intent'li
    # gerçek bir ADS adayı kur ki erken '0 keyword' dönüşü yolu kısaltmasın
    from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter

    ads_kw = _mk_candidate(db_session, run, ws, make_keyword,
                           "ads fail closed", "ADS")
    db_session.add(IntentAnalysis(
        scoring_run_id=run.id, keyword_id=ads_kw.id, channel="ADS",
        intent_type="commercial", confidence_score=0.9, is_passed=True,
    ))
    run.execution_manifest = None
    db_session.commit()
    with pytest.raises(StrategySnapshotMissingError):
        AdsPreFilter(db_session, ai).filter_candidates(run.id)
    assert ai.prompts == []


def test_v21_intent_fallback_leaves_strategy_fit_null(
    db_session, make_workspace, make_scoring_run, make_keyword
):
    """Parse/eksik fallback'te strategy_fit NULL kalır (sessizce 0 yok)."""

    class _BrokenAI:
        def complete_json(self, prompt, **kwargs):
            return "bozuk json ["

    ws = make_workspace("v21 fallback")
    run = make_scoring_run(brand_profile_id=ws.id, status="channel_assigning")
    run.algorithm_version = "v2_1"
    run.execution_manifest = _v21_manifest()
    kw = _mk_candidate(db_session, run, ws, make_keyword,
                       "saç serumu önerisi", "SEO")

    _analyzer(db_session, _BrokenAI()).analyze_candidates(run.id, "SEO")
    row = db_session.query(IntentAnalysis).filter_by(
        scoring_run_id=run.id, keyword_id=kw.id, channel="SEO"
    ).one()
    assert row.source == "fallback"
    assert row.strategy_fit is None
    assert row.is_passed is True  # SEO override — elenmez, boost almaz


class TestPromptContracts:
    """Codex Faz D 2. tur #3: v2 prompt'ları HASH ile bayt-kilitli; v2_1
    ADS/SOCIAL blokları doğrudan assert edilir."""

    # Sürüm-anahtarlı DEĞİŞMEZ prompt hash registry'si (Codex 4. tur #1):
    # mevcut kayıtlar APPEND-ONLY — bilinçli prompt değişikliğinde mevcut
    # satır DÜZENLENMEZ, yeni PROMPT_CONFIG_VERSION anahtarıyla YENİ kayıt
    # eklenir. Test canlı sürümün registry'de kayıtlı olmasını ve canlı
    # hash'lerin O sürümün kaydıyla eşleşmesini zorlar; böylece hash
    # değişikliği yapısal olarak yeni sürüm kaydına bağlanır (tam otomatik
    # zorlama mümkün değil — mevcut kaydı düzenlemek code-review ihlalidir).
    PROMPT_HASH_REGISTRY = {
        "2026-07-23-v21d": {
            "SEO_INTENT": "0711b52a9fc0a0636aa37d64a7b0d27b52605461bd6fd9c2887626b1c035f6df",
            "ADS_INTENT": "8929d93b5a3e570a46deb3eaa1b2c6d3873f8b109d13ed3718985c2e238b3415",
            "ADS_PREFILTER": "6935722a2bb596bbb523164787e52179d126c3b1523400793fcfafa30278c75c",
            "SOCIAL_PREFILTER": "94b109a54b86230bf1a161070a38764ddfc01565ff16084439924189080e6c58",
        },
    }
    FIXED_KW = [{"id": 1, "keyword": "sabit kelime"}]

    @staticmethod
    def _sha(text: str) -> str:
        import hashlib

        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def test_v2_prompts_byte_locked(self):
        from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter
        from app.core.channel.pre_filters.social_prefilter import SocialPreFilter
        from app.core.constants import PROMPT_CONFIG_VERSION

        # Canlı sürüm registry'de KAYITLI olmalı — yeni hash yeni sürüm
        # kaydı gerektirir (registry append-only sözleşmesi)
        assert PROMPT_CONFIG_VERSION in self.PROMPT_HASH_REGISTRY, (
            f"PROMPT_CONFIG_VERSION={PROMPT_CONFIG_VERSION!r} registry'de yok — "
            "prompt değiştiyse YENİ sürüm anahtarıyla yeni kayıt ekleyin; "
            "mevcut kayıtları DÜZENLEMEYİN."
        )
        expected = self.PROMPT_HASH_REGISTRY[PROMPT_CONFIG_VERSION]
        # Registry bütünlüğü: her sürüm kaydı 4 prompt'u da kapsar ve hiçbir
        # iki sürüm birebir aynı hash setini taşımaz (kopyala-yapıştır sürüm
        # şişirmesi anlamsızlaşır)
        keys = {"SEO_INTENT", "ADS_INTENT", "ADS_PREFILTER", "SOCIAL_PREFILTER"}
        assert all(set(v) == keys for v in self.PROMPT_HASH_REGISTRY.values())
        fingerprints = [tuple(sorted(v.items()))
                        for v in self.PROMPT_HASH_REGISTRY.values()]
        assert len(fingerprints) == len(set(fingerprints))

        class _A:
            pass

        ia = IntentAnalyzer(None, _A())
        assert self._sha(ia._build_intent_prompt(
            self.FIXED_KW, channel="SEO", brand_terms=None,
            product_definition="Sabit ürün tanımı",
        )) == expected["SEO_INTENT"]
        assert self._sha(ia._build_intent_prompt(
            self.FIXED_KW, channel="ADS", brand_terms=None,
            product_definition=None,
        )) == expected["ADS_INTENT"]

        ads = AdsPreFilter(None, None)
        ads._product_definition = "Sabit ürün tanımı"
        assert self._sha(
            ads._build_filter_prompt(self.FIXED_KW)
        ) == expected["ADS_PREFILTER"]

        soc = SocialPreFilter(None, None)
        soc._product_definition = "Sabit ürün tanımı"
        assert self._sha(
            soc._build_filter_prompt(self.FIXED_KW)
        ) == expected["SOCIAL_PREFILTER"]

    def test_v21_ads_prefilter_prompt_blocks(self):
        from app.core.channel.pre_filters.ads_prefilter import AdsPreFilter

        ads = AdsPreFilter(None, None)
        ads._product_definition = "Sabit ürün tanımı"
        ads._algorithm_version = "v2_1"
        ads._target_audience = "35+ kadınlar"
        prompt = ads._build_filter_prompt(self.FIXED_KW)
        assert "HEDEF KİTLE" in prompt
        assert "35+ kadınlar" in prompt
        assert "v2.1 SINIF NETLEŞTİRMESİ" in prompt
        assert "ürün-ALANI" in prompt

    def test_v21_social_prefilter_prompt_blocks(self):
        from app.core.channel.pre_filters.social_prefilter import SocialPreFilter

        soc = SocialPreFilter(None, None)
        soc._product_definition = "Sabit ürün tanımı"
        soc._algorithm_version = "v2_1"
        soc._social_mode = "hype"
        prompt = soc._build_filter_prompt(self.FIXED_KW)
        assert "MÜŞTERİ ÜRÜN TANIMI" in prompt
        assert "Sabit ürün tanımı" in prompt
        assert "SOSYAL STRATEJİ MODU: hype" in prompt


class TestSocialMomentumSource:
    def _score(self, db, run, kw, derived):
        db.add(KeywordScore(
            scoring_run_id=run.id, keyword_id=kw.id,
            social_score=10, social_rank=1,
            metrics_snapshot={"monthly_volume": 100, "trend_3m": 10.0,
                              "trend_12m": 0.0, "derived": derived},
        ))
        db.commit()

    def test_v21_uses_clamped_t3_and_fails_fast_on_missing(
        self, db_session, make_workspace, make_scoring_run, make_keyword
    ):
        from app.core.channel.pre_filters.social_prefilter import (
            MomentumSnapshotMissingError,
            SocialPreFilter,
        )

        ws = make_workspace("v21 social")
        run = make_scoring_run(brand_profile_id=ws.id,
                               status="channel_assigning")
        run.algorithm_version = "v2_1"
        kw = make_keyword("saç trendi", brand_profile_id=ws.id)
        self._score(db_session, run, kw, {"t3": 2.5, "mb": 0.9})

        pf = SocialPreFilter(db_session, ai_service=None)
        m_map = pf._load_m_map_v21(run.id)
        assert m_map[kw.id] == 1.0  # min(max(2.5,0),1) — MB(0.9) DEĞİL

        # derived.t3=0 KORUNUR (eksik sayılmaz)
        kw2 = make_keyword("sıfır trend", brand_profile_id=ws.id)
        self._score(db_session, run, kw2, {"t3": 0, "mb": 0.5})
        assert pf._load_m_map_v21(run.id)[kw2.id] == 0.0

        # t3 alanı YOKSA fail-fast
        kw3 = make_keyword("eksik t3", brand_profile_id=ws.id)
        self._score(db_session, run, kw3, {"mb": 0.5})
        with pytest.raises(MomentumSnapshotMissingError,
                           match="MOMENTUM_SNAPSHOT_MISSING"):
            pf._load_m_map_v21(run.id)

    def test_extra_data_audit_keys_by_metric(self):
        """Codex Faz D 2. tur #2: v2_1'de m + momentum_metric='m' + replay
        için mb BİRLİKTE; v2'de mb + momentum_metric='mb' (M/MB replay'i
        yanılmaz)."""
        import json as _json

        from app.core.channel.pre_filters.social_prefilter import SocialPreFilter

        response = {"results": [{
            "keyword_id": 1,
            "dims": {"opinion_discussion": 0, "curiosity_comparison": 0,
                     "agenda_theme": 0},
            "reason": "x", "meta": {"hook": "h", "scenario_note": "s"},
        }]}

        v21 = SocialPreFilter(None, None)
        v21._momentum_metric = "m"
        v21._mb_map = {1: 0.8}
        v21._mb_audit_map = {1: 0.4}
        row = v21._parse_ai_response(_json.loads(_json.dumps(response)), [])[0]
        assert row["extra_data"]["momentum_metric"] == "m"
        assert row["extra_data"]["m"] == 0.8   # KULLANILAN metrik (M)
        assert row["extra_data"]["mb"] == 0.4  # replay için MB birlikte
        assert row["extra_data"]["mb_available"] is True
        assert row["is_kept"] is True          # M(0.8) > eşik — rescue

        # Audit MB eksikse SAHTE 0.0 yazılmaz — null + mb_available=False
        v21_missing = SocialPreFilter(None, None)
        v21_missing._momentum_metric = "m"
        v21_missing._mb_map = {1: 0.8}
        v21_missing._mb_audit_map = {1: None}
        row = v21_missing._parse_ai_response(
            _json.loads(_json.dumps(response)), []
        )[0]
        assert row["extra_data"]["mb"] is None
        assert row["extra_data"]["mb_available"] is False

        v2 = SocialPreFilter(None, None)
        v2._momentum_metric = "mb"
        v2._mb_map = {1: 0.2}
        v2._mb_audit_map = None
        row = v2._parse_ai_response(_json.loads(_json.dumps(response)), [])[0]
        assert row["extra_data"]["momentum_metric"] == "mb"
        assert row["extra_data"]["mb"] == 0.2
        assert "m" not in row["extra_data"]
        assert row["is_kept"] is False  # MB(0.2) <= eşik + sınıf 0

    def test_v21_audit_mb_rejects_nonfinite_and_out_of_range(
        self, db_session, make_workspace, make_scoring_run, make_keyword
    ):
        """Codex 4. tur #2: NaN/Infinity/aralık dışı/bool MB değerleri
        audit'te GEÇERLİ sayılmaz — None + mb_available=False."""
        from app.core.channel.pre_filters.social_prefilter import SocialPreFilter

        # NaN/Infinity Postgres JSON'a yazılamaz — geçerlilik kuralı saf
        # yardımcı üzerinden doğrudan test edilir (savunma katmanı)
        valid = SocialPreFilter._valid_audit_mb
        assert valid(float("nan")) is None
        assert valid(float("inf")) is None
        assert valid(float("-inf")) is None
        assert valid(-0.2) is None
        assert valid(1.5) is None
        assert valid(True) is None
        assert valid("yüksek") is None
        assert valid(None) is None
        assert valid(0.0) == 0.0
        assert valid(1.0) == 1.0
        assert valid("0.37") == 0.37  # JSON'dan string gelse bile parse edilir

        # DB yolu: JSON-uyumlu geçersiz değerler harita üzerinden de None
        ws = make_workspace("v21 mb validity")
        run = make_scoring_run(brand_profile_id=ws.id,
                               status="channel_assigning")
        run.algorithm_version = "v2_1"
        cases = {
            "mb negatif": -0.2,
            "mb bir üstü": 1.5,
            "mb bool": True,
            "mb string bozuk": "yüksek",
            "mb geçerli orta": 0.37,
        }
        kws = {}
        for name, mb in cases.items():
            kw = make_keyword(name, brand_profile_id=ws.id)
            kws[name] = kw
            self._score(db_session, run, kw, {"t3": 0.1, "mb": mb})

        audit = SocialPreFilter(db_session, None)._load_mb_audit_map_v21(run.id)
        for name in ("mb negatif", "mb bir üstü", "mb bool", "mb string bozuk"):
            assert audit[kws[name].id] is None, name
        assert audit[kws["mb geçerli orta"].id] == 0.37

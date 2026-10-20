"""Codex v18-6: patron karşılaştırma/import araçlarının canned testleri.

Ölçüm aracının kendi dürüstlüğü: kapılar yanlış tasarımı reddediyor mu,
metrikler (306-negatif FP'si dahil) doğru mu, nedenler doğru ayrışıyor mu,
importer sessiz geçiyor mu?
"""
from types import SimpleNamespace

import pytest

from app.core.benchmark.patron_compare import (
    artifact_gate_problems,
    compute_metrics,
    diagnose,
    raw_view_metrics,
    run_design_problems,
)
from app.core.benchmark.patron_dataset import (
    DatasetError,
    parse_mark,
    stable_imported_at,
    strict_number,
    trend_scale_evidence,
)


# ── Metrikler ──

class TestMetrics:
    def test_pick_from_not_selected_counts_as_fp(self):
        """v17-1 sözleşmesi: 306 seçilmemiş-temizden seçim FP'dir —
        precision artık şişirilemez."""
        positives = {"a", "b"}
        negatives = {"n1", "n2", "n3"}  # seçilmemiş-temiz dahil
        system = {"a", "n1"}  # n1 = seçilmemiş-temizden seçim
        m = compute_metrics(positives, negatives, system)
        assert m["tp"] == 1 and m["fp"] == 1 and m["fn"] == 1 and m["tn"] == 2
        assert m["precision"] == pytest.approx(0.5)
        assert m["recall"] == pytest.approx(0.5)
        assert m["jaccard"] == pytest.approx(1 / 3)

    def test_raw_view_570_includes_fp_tn(self):
        """v18-3: ham görünüm yalnız pozitif-recall değil — 570 satırın
        tamamı, FP/TN dahil."""
        doc = {
            "raw_labels": [
                {"keyword": "işaretli kelime", "clean_row_no": 2,
                 "ads": True, "seo": False, "social": False},
                {"keyword": "boş kelime", "clean_row_no": 3,
                 "ads": False, "seo": False, "social": False},
            ],
            "clean_source_rows": [
                {"row_no": 2, "keyword": "işaretli kelime"},
                {"row_no": 3, "keyword": "boş kelime"},
                {"row_no": 4, "keyword": "seçilmemiş kelime"},
            ],
        }
        pools = {"ADS": {"secilmemis kelime"}, "SEO": set(), "SOCIAL": set()}

        def norm(t):
            return (t.lower().replace("ş", "s").replace("ç", "c")
                    .replace("ı", "i"))

        out = raw_view_metrics(doc, pools, norm)
        ads = out["ADS"]
        assert ads["rows"] == 3
        # işaretli kaçtı (FN), seçilmemiş seçildi (FP), boş seçilmedi (TN)
        assert ads["fn"] == 1 and ads["fp"] == 1 and ads["tn"] == 1
        assert ads["tp"] == 0

    def test_raw_view_twins_do_not_overwrite(self):
        """v19-1 REGRESYONU: norm-anahtarlı sözlük ikizleri eziyordu
        (ADS 128≠125). Satır bağlantısıyla ham pozitif sayısı GERÇEK
        işaret sayısına eşittir — çelişkili ikizde bile."""
        doc = {
            "raw_labels": [
                {"keyword": "canlı borsa", "clean_row_no": 2,
                 "ads": True, "seo": False, "social": False},
                {"keyword": "canli borsa", "clean_row_no": 3,
                 "ads": False, "seo": False, "social": False},
            ],
            "clean_source_rows": [
                {"row_no": 2, "keyword": "canlı borsa"},
                {"row_no": 3, "keyword": "canli borsa"},
            ],
        }
        pools = {"ADS": set(), "SEO": set(), "SOCIAL": set()}

        def norm(t):
            return (t.lower().replace("ş", "s").replace("ç", "c")
                    .replace("ı", "i"))

        out = raw_view_metrics(doc, pools, norm)
        assert out["ADS"]["fn"] == 1 and out["ADS"]["tn"] == 1
        assert out["ADS"]["rows"] == 2

    def test_raw_view_requires_row_link(self):
        doc = {
            "raw_labels": [{"keyword": "x", "ads": True,
                            "seo": False, "social": False}],
            "clean_source_rows": [{"row_no": 2, "keyword": "x"}],
        }
        with pytest.raises(ValueError, match="satır bağlantısı yok"):
            raw_view_metrics(doc, {"ADS": set(), "SEO": set(),
                                   "SOCIAL": set()}, lambda t: t)

    def test_aggregate_usage_includes_embedding_stage(self):
        """v19-5: tam-run maliyeti stage kırılımlı — embedding dahil."""
        from app.core.benchmark.patron_compare import aggregate_usage

        events = [
            {"stage": "intent", "model": "gemini-3.5-flash",
             "prompt_tokens": 1000, "candidates_tokens": 100,
             "thoughts_tokens": 50, "retry_reason": None,
             "price_snapshot": {"input_per_m": 1.5, "output_per_m": 9.0}},
            {"stage": "embedding", "model": "models/gemini-embedding-2",
             "prompt_tokens": 2000, "candidates_tokens": None,
             "thoughts_tokens": None, "retry_reason": None,
             "price_snapshot": {"input_per_m": 0.2, "output_per_m": 0.0}},
        ]
        out = aggregate_usage(events)
        assert out["total_requests"] == 2
        stages = {b["stage"] for b in out["stages"]}
        assert "embedding" in stages and "intent" in stages
        assert out["total_estimated_usd"] > 0


# ── Kapılar ──

def _mk_run(**over):
    base = dict(
        brand_profile_id=21, status="channel_assigned",
        ads_capacity=123, seo_capacity=52, social_capacity=64,
        keyword_selection_mode="all",
        enable_ads=True, enable_seo=True, enable_social=True,
        skip_relevance=False, created_at=None,
        execution_manifest={
            "git_sha": "abc", "model": "gemini-3.5-flash",
            "prompt_config_version": "v1",
            "policy_snapshot": {
                "profile_exclude_themes": [],
                "topic_terms_approved": [],
                "competitor_terms_approved": ["fintables"],
                "competitor_channel_policy": {
                    "ads": "block", "seo": "block", "social": "block"},
            },
        },
    )
    base.update(over)
    return SimpleNamespace(**base)


DOC = {
    "workspace_id": 21,
    "source_final_xlsx_sha256": "SHA",
    "imported_at": "2026-07-17T00:00:00+00:00",
    "counts": {"ads": 123, "seo": 52, "social": 64,
               "clean_universe_units": 540},
}


class TestRunDesignGate:
    def test_clean_design_passes(self):
        assert run_design_problems(_mk_run(), DOC, set(range(540)), set(range(540))) == []

    def test_wrong_capacity_refused(self):
        problems = run_design_problems(_mk_run(ads_capacity=60), DOC, set(range(540)), set(range(540)))
        assert any("ADS kapasitesi 60" in p for p in problems)

    def test_wrong_mode_and_status_refused(self):
        problems = run_design_problems(
            _mk_run(status="scored", keyword_selection_mode="specific"),
            DOC, set(range(540)), set(range(540)))
        text = "\n".join(problems)
        assert "status='scored'" in text and "specific" in text

    def test_missing_relevance_coverage_refused(self):
        """v19-4: SAYI değil KİMLİK — ilgisiz relevance satırı eksik
        benchmark kelimesini maskeleyemez."""
        score_kids = set(range(540))
        # 539 doğru + 1 İLGİSİZ kimlik: sayı 540 ama kimlik seti bozuk
        relevance_kids = set(range(539)) | {9999}
        problems = run_design_problems(_mk_run(), DOC,
                                       relevance_kids, score_kids)
        assert any("kimlik kapsaması bozuk: eksik=1 fazla=1" in p
                   for p in problems)

    def test_mode_none_refused(self):
        """v19-2: None da GEÇEMEZ — sözleşme açıkça 'all'."""
        problems = run_design_problems(
            _mk_run(keyword_selection_mode=None), DOC,
            set(range(540)), set(range(540)))
        assert any("keyword_selection_mode=None" in p for p in problems)

    def test_empty_channel_policy_refused(self):
        """v19-3: boş {} politika sahte-temiz GEÇEMEZ — üç anahtar da
        'block' şart."""
        run = _mk_run()
        run.execution_manifest["policy_snapshot"][
            "competitor_channel_policy"] = {}
        problems = run_design_problems(run, DOC,
                                       set(range(540)), set(range(540)))
        assert any("rakip politikası eksik/block-değil" in p
                   for p in problems)

    def test_dirty_policy_snapshot_refused(self):
        run = _mk_run()
        run.execution_manifest["policy_snapshot"]["profile_exclude_themes"] = \
            ["yatırım tavsiyesi"]
        run.execution_manifest["policy_snapshot"]["competitor_terms_approved"] = []
        problems = run_design_problems(run, DOC, set(range(540)), set(range(540)))
        text = "\n".join(problems)
        assert "exclude_themes DOLU" in text
        assert "fintables" in text

    def test_missing_snapshot_refused(self):
        run = _mk_run()
        run.execution_manifest.pop("policy_snapshot")
        problems = run_design_problems(run, DOC, set(range(540)), set(range(540)))
        assert any("policy_snapshot yok" in p for p in problems)

    def test_channel_disabled_and_skip_relevance_refused(self):
        problems = run_design_problems(
            _mk_run(enable_social=False, skip_relevance=True), DOC,
            set(range(540)), set(range(540)))
        text = "\n".join(problems)
        assert "SOCIAL kanalı kapalı" in text
        assert "skip_relevance" in text


class TestArtifactGate:
    def test_universe_equality_enforced(self):
        run = _mk_run()
        problems = artifact_gate_problems(
            run, DOC, run_norms={"a", "b"},
            universe_norms={"a", "b", "c"}, final_xlsx_sha="SHA")
        assert any("evren uyuşmazlığı" in p for p in problems)

    def test_sha_and_manifest_enforced(self):
        run = _mk_run(execution_manifest={})
        problems = artifact_gate_problems(
            run, DOC, run_norms={"a"}, universe_norms={"a"},
            final_xlsx_sha="BAŞKA")
        text = "\n".join(problems)
        assert "SHA" in text and "manifest" in text


# ── Neden ayrımı ──

def _mk_state(channel="ADS", *, pools=(), candidates=None, pf=None, ia=None):
    return SimpleNamespace(
        pools={channel: set(pools), "SEO": set(), "SOCIAL": set()},
        candidates={channel: candidates or {}, "SEO": {}, "SOCIAL": {}},
        prefilters={channel: {1: pf} if pf else {}, "SEO": {}, "SOCIAL": {}},
        intents={channel: {1: ia} if ia else {}, "SEO": {}, "SOCIAL": {}},
    )


class TestDiagnose:
    DETAIL = {1: {"raw": 30.0, "rel": 0.8, "adj": 24.0, "raw_rank": 5}}

    def test_policy_and_competitor_separated(self):
        pf_comp = SimpleNamespace(extra_data={"reason_code": "COMPETITOR_TERM"},
                                  is_kept=False, ai_class=None,
                                  transfer_channel=None, is_fallback=False)
        state = _mk_state(candidates={1: 3}, pf=pf_comp)
        why, _ = diagnose(1, "n", "ADS", state, {1: 3}, self.DETAIL, 10, 120)
        assert why == "rakip_hard_block"

        pf_theme = SimpleNamespace(
            extra_data={"reason_code": "BRAND_EXCLUDED_THEME"},
            is_kept=False, ai_class=None, transfer_channel=None,
            is_fallback=False)
        state = _mk_state(candidates={1: 3}, pf=pf_theme)
        why, _ = diagnose(1, "n", "ADS", state, {1: 3}, self.DETAIL, 10, 120)
        assert why == "policy_blok(BRAND_EXCLUDED_THEME)"

    def test_intent_and_prefilter_and_capacity(self):
        ia_fail = SimpleNamespace(is_passed=False, intent_type="informational",
                                  source="ai")
        state = _mk_state(candidates={1: 3}, ia=ia_fail)
        why, _ = diagnose(1, "n", "ADS", state, {1: 3}, self.DETAIL, 10, 120)
        assert why == "intent_reddi"

        pf_elim = SimpleNamespace(extra_data={}, is_kept=False, ai_class=-1,
                                  transfer_channel=None, is_fallback=True)
        ia_ok = SimpleNamespace(is_passed=True, intent_type="commercial",
                                source="ai")
        state = _mk_state(candidates={1: 3}, pf=pf_elim, ia=ia_ok)
        why, _ = diagnose(1, "n", "ADS", state, {1: 3}, self.DETAIL, 10, 120)
        assert why == "prefilter_eledi(sinif=-1 +fallback)"

        pf_ok = SimpleNamespace(extra_data={}, is_kept=True, ai_class=1,
                                transfer_channel=None, is_fallback=False)
        state = _mk_state(candidates={1: 3}, pf=pf_ok, ia=ia_ok)
        why, _ = diagnose(1, "n", "ADS", state, {1: 3}, self.DETAIL, 10, 120)
        assert why == "kapasite_alti"

    def test_not_candidate_split_by_ranks(self):
        # raw_rank pencere içi ama adj_rank dışı → relevance kaybı
        detail = {1: {"raw": 30.0, "rel": 0.1, "adj": 3.0, "raw_rank": 5}}
        state = _mk_state()
        why, _ = diagnose(1, "n", "ADS", state, {1: 500}, detail, 10, 120)
        assert why == "relevance_sira_kaybi"
        # raw_rank da dışarıda → ham skor
        detail = {1: {"raw": 1.0, "rel": 0.9, "adj": 0.9, "raw_rank": 400}}
        why, _ = diagnose(1, "n", "ADS", state, {1: 500}, detail, 10, 120)
        assert why == "ham_skor_dusuk"


# ── Importer yardımcıları ──

class TestDatasetHelpers:
    def test_strict_number_refuses_garbage(self):
        with pytest.raises(DatasetError, match="çevrilemedi"):
            strict_number("N/A", row=7, field="hacim")
        with pytest.raises(DatasetError, match="boş"):
            strict_number(None, row=7, field="hacim")
        assert strict_number(None, row=7, field="comp",
                             allow_none=True) is None

    def test_parse_mark_only_x(self):
        assert parse_mark("x", row=3, channel="ADS") is True
        assert parse_mark("X", row=3, channel="ADS") is True
        assert parse_mark(None, row=3, channel="ADS") is False
        assert parse_mark("  ", row=3, channel="ADS") is False
        with pytest.raises(DatasetError, match="beklenmeyen işaret"):
            parse_mark("evet", row=3, channel="ADS")

    def test_trend_evidence_two_signals(self):
        ok = trend_scale_evidence([-0.18, 1.5, 0.22, -0.97, 47.0])
        assert ok["ok"] is True
        # Tam-yüzde okuması (S2 başarısız: hepsi ≥1)
        bad = trend_scale_evidence([18.0, 150.0, 22.0, 97.0])
        assert bad["ok"] is False

    def test_stable_imported_at(self):
        existing = {"source_final_xlsx_sha256": "F",
                    "source_raw_xlsx_sha256": "R",
                    "imported_at": "2026-07-17T00:00:00+00:00"}
        # İçerik aynı → damga korunur (önceki run geçersizleşemez)
        assert stable_imported_at(existing, "F", "R", "YENI") == \
            "2026-07-17T00:00:00+00:00"
        # İçerik değişti → yeni damga
        assert stable_imported_at(existing, "F2", "R", "YENI") == "YENI"
        assert stable_imported_at(None, "F", "R", "YENI") == "YENI"
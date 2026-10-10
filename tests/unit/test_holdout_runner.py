"""Holdout / tekrar-koşusu güvenlik sözleşmelerinin doğrudan testleri.

Codex 4. tur #4: "991 test yeşil ama holdout runner'ı sınanmıyor — ilk
bulgunun yeşil suite içinde saklanmasının nedeni bu." Bu dosya o boşluğu
kapatır: üzerine-yazma reddi, sözleşme uyuşmazlığı reddi, dondurulmuş girdi
reddi, E1↔E2 karşılaştırma doğruluğu ve İLK ARTIFACT'IN BAYT BAYT
DEĞİŞMEDİĞİ doğrudan sınanır.
"""
import json

import pytest

from app.core.screening.holdout import (
    HOLDOUT_CONTRACT,
    HoldoutError,
    guard_no_overwrite,
    repeat_comparison,
    verify_contract_match,
    verify_same_frozen_inputs,
    view_rows_from_artifact,
)
from app.core.screening.manifest import build_manifest, seal_artifact


def _row(kid, ads, seo=0, social=0, unresolved=()):
    return {
        "keyword_id": kid, "keyword": f"k{kid}",
        "mean_fit": {}, "passing": {}, "context_disagreement": {},
        "uncertain": False, "unresolved_views": list(unresolved),
        "raw_views": {"A": {"ads": ads, "seo": seo, "social": social},
                      "B": {"ads": ads, "seo": seo, "social": social}},
    }


def _sealed_views_doc(rows, *, universe_sha="u" * 64, context_sha="c" * 64,
                      **overrides):
    params = dict(prompt_version=HOLDOUT_CONTRACT["prompt_version"],
                  temperature=HOLDOUT_CONTRACT["temperature"],
                  batch_size=HOLDOUT_CONTRACT["batch_size"])
    params.update({k: v for k, v in overrides.items()
                   if k in ("prompt_version", "temperature", "batch_size")})
    extra = {
        "kind": "sticky_raw_views",
        "plan_salts": list(HOLDOUT_CONTRACT["views"]),
        "virtual_buckets": HOLDOUT_CONTRACT["virtual_buckets"],
        "prompt_version_used": params["prompt_version"],
        "batch_size": params["batch_size"],
        "temperature_override": params["temperature"],
        "universe_sha256": universe_sha,
        "context_sha256": context_sha,
    }
    extra.update({k: v for k, v in overrides.items() if k in
                  ("plan_salts", "virtual_buckets")})
    manifest = build_manifest(
        provider="deepseek", model=overrides.get("model",
                                                 HOLDOUT_CONTRACT["model"]),
        seed=0, dataset_slug="gr7", generated_at="T",
        universe_size=len(rows), extra=extra, **params)
    return seal_artifact(
        {"manifest": manifest,
         "report": {"gr7": {"merged_results": {"full": rows}}}})


class TestOverwriteGuard:
    def test_refuses_when_any_output_exists(self, tmp_path):
        existing = tmp_path / "holdout_gr7.json"
        existing.write_text("{}", encoding="utf-8")
        missing = tmp_path / "holdout_gr7_repeat.json"
        with pytest.raises(HoldoutError, match="kanıtını siler"):
            guard_no_overwrite([str(existing), str(missing)])

    def test_allows_when_all_outputs_missing(self, tmp_path):
        guard_no_overwrite([str(tmp_path / "a.json"),
                            str(tmp_path / "b.json")])


class TestContractVerification:
    def test_accepts_matching_contract(self):
        doc = _sealed_views_doc([_row(1, 2)])
        verify_contract_match(doc, HOLDOUT_CONTRACT)

    @pytest.mark.parametrize("override,field", [
        ({"prompt_version": "SCR-2026-07-27-v2"}, "prompt_version"),
        ({"temperature": 0.3}, "temperature"),
        ({"batch_size": 30}, "batch_size"),
        ({"model": "gemini-3.5-flash-lite"}, "model"),
        ({"plan_salts": ["baska-salt-a", "baska-salt-b"]}, "plan_salts"),
        ({"virtual_buckets": 64}, "virtual_buckets"),
    ])
    def test_rejects_each_mismatch(self, override, field):
        doc = _sealed_views_doc([_row(1, 2)], **override)
        with pytest.raises(HoldoutError, match=field):
            verify_contract_match(doc, HOLDOUT_CONTRACT)

    def test_rejects_mismatched_frozen_inputs(self):
        doc = _sealed_views_doc([_row(1, 2)], universe_sha="x" * 64)
        with pytest.raises(HoldoutError, match="universe_sha256"):
            verify_same_frozen_inputs(doc, universe_sha256="u" * 64,
                                      context_sha256="c" * 64)
        with pytest.raises(HoldoutError, match="context_sha256"):
            verify_same_frozen_inputs(
                _sealed_views_doc([_row(1, 2)], context_sha="y" * 64),
                universe_sha256="u" * 64, context_sha256="c" * 64)

    def test_frozen_contract_values_are_locked(self):
        """a925f78'de dondurulan değerler — değişirse bu test kırılır."""
        assert HOLDOUT_CONTRACT["name"] == "HOLDOUT-GR7-2026-07-28"
        assert HOLDOUT_CONTRACT["model"] == "deepseek-v4-flash"
        assert HOLDOUT_CONTRACT["prompt_version"] == "SCR-2026-07-27-v3a"
        assert HOLDOUT_CONTRACT["temperature"] == 0.0
        assert HOLDOUT_CONTRACT["batch_size"] == 10
        assert HOLDOUT_CONTRACT["budget_multiplier"] == 3
        assert HOLDOUT_CONTRACT["virtual_buckets"] == 128


class TestViewLoading:
    def test_loads_views_and_rejects_missing_scenario(self):
        doc = _sealed_views_doc([_row(1, 2, unresolved=["B"]),
                                 _row(2, 1)])
        views = view_rows_from_artifact(doc, "gr7", "full")
        assert [r.keyword_id for r in views["A"]] == [1, 2]
        assert views["A"][0].unresolved is False
        assert views["B"][0].unresolved is True
        with pytest.raises(HoldoutError, match="senaryosunu taşımıyor"):
            view_rows_from_artifact(doc, "gr7", "repeat")

    def test_first_artifact_file_is_not_modified(self, tmp_path):
        """İlk koşu kanıtı SALT OKUNUR: okuma+karşılaştırma baytları değiştirmez."""
        doc = _sealed_views_doc([_row(1, 2), _row(2, 1), _row(3, 0)])
        path = tmp_path / "sticky_views_gr7.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        before = path.read_bytes()

        loaded = json.loads(path.read_text(encoding="utf-8"))
        first = view_rows_from_artifact(loaded, "gr7", "full")
        repeat_comparison(
            first, first,
            budgets={"ADS": 2, "SEO": 2, "SOCIAL": 2},
            positives={"ADS": [1], "SEO": [1], "SOCIAL": [1]},
            raw_ranks={ch: {1: 1, 2: 2, 3: 3}
                       for ch in ("ADS", "SEO", "SOCIAL")},
            multiplier=3, jaccard_threshold=0.78, pass_flip_limit=0.10)
        assert path.read_bytes() == before


class TestRepeatComparison:
    def _views(self, fits_by_kid):
        from types import SimpleNamespace

        return {label: [SimpleNamespace(
            keyword_id=k, keyword=f"k{k}", ads_fit=f, seo_fit=f,
            social_fit=f, unresolved=False)
            for k, f in fits_by_kid.items()] for label in ("A", "B")}

    def _kwargs(self, n=4):
        return dict(
            budgets={"ADS": 1, "SEO": 1, "SOCIAL": 1},
            positives={"ADS": [1], "SEO": [1], "SOCIAL": [1]},
            raw_ranks={ch: {k: k for k in range(1, n + 1)}
                       for ch in ("ADS", "SEO", "SOCIAL")},
            multiplier=3, jaccard_threshold=0.78, pass_flip_limit=0.10)

    def test_identical_runs_pass_with_perfect_jaccard(self):
        v = self._views({1: 2, 2: 1, 3: 1, 4: 0})
        out = repeat_comparison(v, v, **self._kwargs())
        assert out["passed"] is True
        assert out["ensemble_pass_flip"] == 0.0
        for ch in ("ADS", "SEO", "SOCIAL"):
            assert out["channels"][ch]["ensemble_jaccard_at_multiplied"] == 1.0
            assert out["channels"][ch]["reach_delta"] == 0

    def test_divergent_runs_fail_jaccard_gate(self):
        first = self._views({1: 2, 2: 2, 3: 0, 4: 0})
        repeat = self._views({1: 0, 2: 0, 3: 2, 4: 2})
        out = repeat_comparison(first, repeat, **self._kwargs())
        assert out["passed"] is False
        assert not out["gates"]["ensemble_jaccard_at_multiplied"]["passed"]
        # pass-flip de yakalanır: 1,2 geçerken elendi; 3,4 elenmişken geçti
        assert not out["gates"]["ensemble_pass_flip"]["passed"]

    def test_metric_is_ensemble_not_single_view(self):
        """G3 dersi: görünümler ayrışsa bile ENSEMBLE aynıysa test geçer."""
        from types import SimpleNamespace

        def mk(fit_a, fit_b):
            return {label: [SimpleNamespace(
                keyword_id=k, keyword=f"k{k}",
                ads_fit=(fit_a if label == "A" else fit_b)[i],
                seo_fit=(fit_a if label == "A" else fit_b)[i],
                social_fit=(fit_a if label == "A" else fit_b)[i],
                unresolved=False)
                for i, k in enumerate((1, 2))] for label in ("A", "B")}

        # İki koşuda da görünümler AYNI biçimde ayrışıyor (A: [2,0], B: [0,2])
        # → tek-görünüm kümeleri farklı olurdu ama ensemble (mean .5/.5) aynı
        first = mk((2, 0), (0, 2))
        repeat = mk((2, 0), (0, 2))
        out = repeat_comparison(
            first, repeat,
            budgets={"ADS": 1, "SEO": 1, "SOCIAL": 1},
            positives={"ADS": [1], "SEO": [1], "SOCIAL": [1]},
            raw_ranks={ch: {1: 1, 2: 2} for ch in ("ADS", "SEO", "SOCIAL")},
            multiplier=1, jaccard_threshold=0.78, pass_flip_limit=0.10)
        assert out["passed"] is True
        assert all(out["channels"][ch]["ensemble_jaccard_at_multiplied"] == 1.0
                   for ch in ("ADS", "SEO", "SOCIAL"))


class TestCalibrationSemantics:
    """Codex 5. tur #1: mühür yetmez — GEÇERLİ mühürlü ama YANLIŞ artifact
    eşik kaynağı olarak kabul edilemez. Her karşı örnek yeniden mühürlenir
    (seal geçerli), reddin SEMANTİK doğrulamadan geldiği garanti edilir."""

    def _calib_doc(self, **mut):
        from app.core.screening.holdout import expected_repeat_metric

        extra = {
            "kind": mut.get("kind", "ensemble_repeat_calibration"),
            "holdout_contract": mut.get("contract_name",
                                        HOLDOUT_CONTRACT["name"]),
            "source_artifact": "benchmark/screening/sticky_views_dijital.json",
            "source_artifact_payload_sha256": mut.get("source_sha", "s" * 64),
        }
        manifest = build_manifest(
            provider="offline", model="analysis", seed=-1,
            dataset_slug=mut.get("dataset", "dijital"), generated_at="T",
            prompt_version=HOLDOUT_CONTRACT["prompt_version"],
            temperature=HOLDOUT_CONTRACT["temperature"],
            batch_size=HOLDOUT_CONTRACT["batch_size"], extra=extra)
        report = {
            "comparison": {"metric": mut.get(
                "metric",
                expected_repeat_metric(HOLDOUT_CONTRACT["budget_multiplier"]))},
            "declared_gates": {
                "ensemble_jaccard_at_3B_min": mut.get("threshold", 0.78),
                "pass_flip_max": mut.get("flip", 0.10),
            },
        }
        return seal_artifact({"manifest": manifest, "report": report})

    def _verify(self, doc, source_doc=None):
        from app.core.screening.holdout import verify_calibration_artifact

        return verify_calibration_artifact(
            doc, contract=HOLDOUT_CONTRACT, expected_dataset="dijital",
            source_doc=source_doc)

    def test_accepts_correct_calibration(self):
        gates = self._verify(self._calib_doc())
        assert gates["ensemble_jaccard_at_3B_min"] == 0.78

    def test_rejects_wrong_dataset_even_if_sealed(self):
        with pytest.raises(HoldoutError, match="dataset_slug"):
            self._verify(self._calib_doc(dataset="gr7"))

    def test_rejects_wrong_kind(self):
        with pytest.raises(HoldoutError, match="kind"):
            self._verify(self._calib_doc(kind="budget_curve"))

    def test_rejects_wrong_holdout_contract_name(self):
        with pytest.raises(HoldoutError, match="holdout_contract"):
            self._verify(self._calib_doc(contract_name="BASKA-SOZLESME"))

    def test_rejects_wrong_metric_signature(self):
        # Tek-görünüm G3 metriğiyle üretilmiş bir "kalibrasyon" reddedilir
        with pytest.raises(HoldoutError, match="metrik"):
            self._verify(self._calib_doc(metric="single-view A-B Jaccard@3B"))

    def test_rejects_wrong_multiplier_via_metric(self):
        from app.core.screening.holdout import expected_repeat_metric

        with pytest.raises(HoldoutError, match="çarpan"):
            self._verify(self._calib_doc(metric=expected_repeat_metric(5)))

    @pytest.mark.parametrize("bad", [None, "0.78", 0.0, 1.0, -0.5])
    def test_rejects_invalid_threshold(self, bad):
        with pytest.raises(HoldoutError, match="eşik"):
            self._verify(self._calib_doc(threshold=bad))

    def test_rejects_source_sha_mismatch(self):
        source = {"artifact_payload_sha256": "x" * 64}
        with pytest.raises(HoldoutError, match="kaynak sticky"):
            self._verify(self._calib_doc(source_sha="s" * 64),
                         source_doc=source)

    def test_accepts_matching_source_sha(self):
        source = {"artifact_payload_sha256": "s" * 64}
        self._verify(self._calib_doc(source_sha="s" * 64), source_doc=source)


class TestFullContractEquality:
    """Codex 5. tur #2: gömülü holdout sözleşmesi varsa BİREBİR eşitlik."""

    def _doc_with_embedded(self, embedded, version=None):
        extra = {
            "kind": "sticky_raw_views",
            "plan_salts": list(HOLDOUT_CONTRACT["views"]),
            "virtual_buckets": HOLDOUT_CONTRACT["virtual_buckets"],
            "holdout_contract": embedded,
            "universe_sha256": "u" * 64, "context_sha256": "c" * 64,
        }
        if version is not None:
            extra["ensemble_contract_version"] = version
        manifest = build_manifest(
            provider="deepseek", model=HOLDOUT_CONTRACT["model"], seed=0,
            dataset_slug="gr7", generated_at="T",
            prompt_version=HOLDOUT_CONTRACT["prompt_version"],
            temperature=HOLDOUT_CONTRACT["temperature"],
            batch_size=HOLDOUT_CONTRACT["batch_size"], extra=extra)
        return seal_artifact(
            {"manifest": manifest,
             "report": {"gr7": {"merged_results": {"full": []}}}})

    def test_accepts_identical_embedded_contract(self):
        verify_contract_match(self._doc_with_embedded(dict(HOLDOUT_CONTRACT)),
                              HOLDOUT_CONTRACT)

    @pytest.mark.parametrize("field,value", [
        ("budget_multiplier", 5),
        ("tiebreak", "relevance -> keyword_id"),
        ("ensemble", "max-fit"),
        ("gates", {"G1_positive_kept_min": 0.5}),
        ("name", "HOLDOUT-ESKI"),
    ])
    def test_rejects_embedded_contract_field_drift(self, field, value):
        embedded = dict(HOLDOUT_CONTRACT)
        embedded[field] = value
        with pytest.raises(HoldoutError, match=f"holdout_contract.{field}"):
            verify_contract_match(self._doc_with_embedded(embedded),
                                  HOLDOUT_CONTRACT)

    def test_rejects_stale_ensemble_contract_version(self):
        doc = self._doc_with_embedded(dict(HOLDOUT_CONTRACT),
                                      version="ENS-2020-01-01-v0")
        with pytest.raises(HoldoutError, match="ensemble_contract_version"):
            verify_contract_match(doc, HOLDOUT_CONTRACT)

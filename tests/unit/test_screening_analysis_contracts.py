"""Analiz sözleşmeleri için doğrudan testler (Codex #7).

Kapsanan sözleşmeler:
1. Manifest fail-closed — gerçekte kullanılan parametreler yazılır,
   üst düzey ile `extra` çelişirse artifact REDDEDİLİR.
2. Sticky batch — üst sınır (<=10) zorlanır, kova kimliği evren
   büyüklüğünden BAĞIMSIZDIR, evren değişimi yalnız yereli etkiler.
3. Churn ölçümü — erişim yalnız İKİ evrende de bulunan pozitiflerde;
   ekleme yönünde yerinden etme ayrı ölçülür.
4. B eğrisi — erişim tek görünümden değil, tüm görünümlerden aralıkla;
   SEO ön-filtresi downstream maliyette AI istekli sayılmaz.
5. Tie-break — `ensemble_ordering` relevance KABUL ETMEZ.
"""
import pytest

from app.core.screening.contract import PROMPT_TEMPLATES
from app.core.screening.ensemble import (
    BatchPlanError,
    VIRTUAL_BUCKETS,
    ensemble_ordering,
    merge_views,
    sticky_plan,
    sticky_plans,
    validate_batch_plan,
    virtual_bucket,
)
from app.core.screening.manifest import (
    ManifestError,
    build_manifest,
    contract_hashes,
    seal_artifact,
    validate_artifact,
)
from app.core.screening.metrics import (
    displacement,
    downstream_requests,
    reach_range,
    restricted_reach,
)

V3A = "SCR-2026-07-27-v3a"
V2 = "SCR-2026-07-27-v2"


def _universe(n, start=1):
    return [{"id": i, "keyword": f"kelime {i}"} for i in range(start, start + n)]


class TestManifestProvenance:
    def _manifest(self, **kw):
        return build_manifest(provider="deepseek", model="m", seed=0,
                              dataset_slug="dijital", generated_at="T", **kw)

    def test_records_actual_parameters_not_contract_defaults(self):
        m = self._manifest(prompt_version=V3A, temperature=0.0, batch_size=10)
        assert m["prompt_version"] == V3A
        assert m["temperature"] == 0.0
        assert m["batch_size"] == 10

    def test_prompt_hash_follows_used_version(self):
        used = self._manifest(prompt_version=V3A)["prompt_template_sha256"]
        default = self._manifest()["prompt_template_sha256"]
        assert used == contract_hashes(V3A)["prompt_template_sha256"]
        assert used != default, "v3a ve v2 aynı hash'i taşıyamaz"
        assert PROMPT_TEMPLATES[V3A] != PROMPT_TEMPLATES[V2]

    def test_temperature_zero_is_recorded_not_treated_as_missing(self):
        # 0.0 falsy'dir; `or` ile yazılırsa sessizce 0.3'e döner
        assert self._manifest(temperature=0.0)["temperature"] == 0.0

    def test_rejects_manifest_contradicting_extra(self):
        m = self._manifest(prompt_version=V2, batch_size=30, temperature=0.3)
        m["extra"] = {"prompt_version_used": V3A, "batch_size": 10,
                      "temperature_override": 0.0}
        doc = seal_artifact({"manifest": m, "report": {}})
        with pytest.raises(ManifestError, match="çelişki"):
            validate_artifact(doc)

    def test_accepts_consistent_manifest(self):
        m = self._manifest(prompt_version=V3A, batch_size=10, temperature=0.0)
        m["extra"] = {"prompt_version_used": V3A, "batch_size": 10,
                      "temperature_override": 0.0}
        validate_artifact(seal_artifact({"manifest": m, "report": {}}))


class TestStickyBatchContract:
    def test_enforces_batch_upper_bound(self):
        with pytest.raises(BatchPlanError, match="üst sınır"):
            sticky_plan(_universe(100), 30, "salt")

    def test_all_batches_within_limit_and_cover_universe(self):
        universe = _universe(671)
        for plan in sticky_plans(universe, 10):
            assert max(len(b) for b in plan) <= 10
            validate_batch_plan(plan, universe, 10)

    def test_bucket_identity_independent_of_universe_size(self):
        small, large = _universe(50), _universe(5000)
        assert len(small) != len(large)
        b_small = {kw["id"]: virtual_bucket(kw["id"], "s") for kw in small}
        b_large = {kw["id"]: virtual_bucket(kw["id"], "s") for kw in large}
        assert all(b_small[k] == b_large[k] for k in b_small), (
            "kova kimliği evren büyüklüğüne bağlı olmamalı"
        )

    def test_deletion_does_not_reshuffle_unaffected_keywords(self):
        universe = _universe(671)
        subset = [kw for kw in universe if kw["id"] % 20 != 0]  # ~%5 silme

        def mates(plan):
            out = {}
            for batch in plan:
                ids = {k["id"] for k in batch}
                for k in batch:
                    out[k["id"]] = ids - {k["id"]}
            return out

        full = mates(sticky_plan(universe, 10, "salt"))
        sub = mates(sticky_plan(subset, 10, "salt"))
        common = set(sub)
        changed = sum(1 for k in common
                      if (full[k] & common) != (sub[k] & common))
        assert changed / len(common) < 0.10, (
            f"sticky plan %5 silmede {changed}/{len(common)} komşuluğu değiştirdi"
        )
        # Sıralı kesme referansı: aynı silmede felaket
        ordered = sorted(universe, key=lambda k: k["id"])
        seq_full = mates([ordered[i:i + 10] for i in range(0, len(ordered), 10)])
        ordered_s = sorted(subset, key=lambda k: k["id"])
        seq_sub = mates([ordered_s[i:i + 10]
                         for i in range(0, len(ordered_s), 10)])
        seq_changed = sum(1 for k in common
                          if (seq_full[k] & common) != (seq_sub[k] & common))
        assert seq_changed > changed * 5

    def test_addition_only_touches_own_bucket(self):
        """Yeni kelimeler YALNIZ kendi kovalarindaki komsuluklari degistirir.

        Zayif kontrol (id iki planda da var mi) yeterli DEGILDIR; etkilenmeyen
        kovadaki kelimelerin komsu KUMESI birebir ayni kalmali.
        """
        universe = _universe(400)
        added = _universe(20, start=10_000)
        grown = universe + added

        def mates(plan):
            out = {}
            for batch in plan:
                ids = {k["id"] for k in batch}
                for k in batch:
                    out[k["id"]] = ids - {k["id"]}
            return out

        before = mates(sticky_plan(universe, 10, "s"))
        after = mates(sticky_plan(grown, 10, "s"))
        touched = {virtual_bucket(kw["id"], "s") for kw in added}
        unaffected = [kw["id"] for kw in universe
                      if virtual_bucket(kw["id"], "s") not in touched]
        assert unaffected, "test anlamli olsun diye etkilenmeyen kova kalmali"
        for kid in unaffected:
            assert before[kid] == after[kid], (
                f"{kid}: etkilenmeyen kovada komsuluk degisti "
                f"{sorted(before[kid])} → {sorted(after[kid])}"
            )
        # Etkilenen kovalarda ise degisim BEKLENIR (aksi halde ekleme
        # hicbir yere dusmemis demektir)
        affected = [kw["id"] for kw in universe
                    if virtual_bucket(kw["id"], "s") in touched]
        assert any(before[kid] != after[kid] for kid in affected)

    def test_validate_batch_plan_catches_violations(self):
        universe = _universe(20)
        with pytest.raises(BatchPlanError, match="üst sınır"):
            validate_batch_plan([universe], universe, 10)
        with pytest.raises(BatchPlanError, match="yinelenen"):
            validate_batch_plan([universe[:5], universe[:5]], universe, 10)
        with pytest.raises(BatchPlanError, match="kapsamıyor"):
            validate_batch_plan([universe[:5]], universe, 10)

    def test_fixed_bucket_count_is_the_documented_constant(self):
        assert VIRTUAL_BUCKETS == 128


class TestChurnMeasurement:
    def test_reach_denominator_excludes_deleted_positives(self):
        # 3 pozitiften biri silinmiş; erişim 2 üzerinden ölçülmeli
        order = [1, 2, 4, 5]
        res = restricted_reach(order, [1, 2, 3], common_ids={1, 2, 4, 5},
                               budget=2)
        assert res["positives"] == 2
        assert res["reached"] == 2
        assert res["recall"] == 1.0

    def test_unrestricted_denominator_would_understate_recall(self):
        order = [1, 2, 4, 5]
        naive = restricted_reach(order, [1, 2, 3], common_ids=None, budget=2)
        assert naive["positives"] == 3 and naive["recall"] < 1.0

    def test_order_is_restricted_before_budget_cut(self):
        # Silinen kelime sıranın başındaysa, kesişimde yer KAPLAMAMALI
        res = restricted_reach([99, 1, 2], [1, 2], common_ids={1, 2},
                               budget=2)
        assert res["reached"] == 2

    def test_displacement_measures_survivors_of_addition(self):
        base_top = {1, 2, 3}
        res = displacement(base_top, [50, 51, 1], budget=3)
        assert res["survivors"] == 1
        assert res["displaced"] == 2
        assert res["displacement_ratio"] == pytest.approx(0.6667, abs=1e-4)


class TestBudgetCurveContract:
    def test_reach_reported_as_range_over_all_views(self):
        orders = {"pairA": [1, 2, 3, 4], "pairB": [4, 3, 2, 1]}
        out = reach_range(orders, [1, 2], {"B": 2})
        assert out["summary"]["B"]["reach_min"] == 0
        assert out["summary"]["B"]["reach_max"] == 2
        assert out["summary"]["B"]["views"] == 2

    def test_single_view_would_hide_the_spread(self):
        out = reach_range({"pairA": [1, 2, 3, 4]}, [1, 2], {"B": 2})
        assert out["summary"]["B"]["reach_min"] == out["summary"]["B"]["reach_max"]

    def test_seo_prefilter_is_not_billed_as_ai_stage(self):
        batches = {"intent": 8, "brand_filter": 5, "prefilter": 6}
        seo = downstream_requests("SEO", 60, batches, ai_free_stages=("prefilter",))
        ads = downstream_requests("ADS", 60, batches)
        assert seo["prefilter"] == 0
        assert ads["prefilter"] == 10
        assert seo["total"] == 8 + 12
        assert ads["total"] == 8 + 12 + 10

    def test_requests_scale_with_candidate_count(self):
        batches = {"intent": 8}
        assert downstream_requests("ADS", 0, batches)["total"] == 0
        assert downstream_requests("ADS", 9, batches)["total"] == 2


class TestTiebreakContract:
    def _rows(self, fits):
        class R:
            def __init__(self, kid, f):
                self.keyword_id = kid
                self.keyword = f"k{kid}"
                self.ads_fit = self.seo_fit = self.social_fit = f
                self.unresolved = False
        return [R(kid, f) for kid, f in fits]

    def test_ordering_rejects_relevance_argument(self):
        merged = merge_views(self._rows([(1, 2), (2, 2)]),
                             self._rows([(1, 2), (2, 2)]))
        with pytest.raises(TypeError):
            ensemble_ordering(merged, "ads", relevance={1: 0.9, 2: 0.1})

    def test_raw_rank_breaks_ties(self):
        merged = merge_views(self._rows([(1, 2), (2, 2)]),
                             self._rows([(1, 2), (2, 2)]))
        assert ensemble_ordering(merged, "ads", raw_rank={1: 5, 2: 1}) == [2, 1]


class TestRankRuleContract:
    def _rows(self, fits):
        class R:
            def __init__(self, kid, f):
                self.keyword_id = kid
                self.keyword = f"k{kid}"
                self.ads_fit = self.seo_fit = self.social_fit = f
                self.unresolved = False
        return [R(kid, f) for kid, f in fits]

    def test_unknown_rule_rejected(self):
        merged = merge_views(self._rows([(1, 2)]), self._rows([(1, 2)]))
        with pytest.raises(ValueError, match="rank_rule"):
            ensemble_ordering(merged, "ads", rank_rule="median")

    def test_max_rule_promotes_split_decision_over_unanimous_one(self):
        # 1: görünümler 2 ve 1 (ortalama 1.5) | 2: iki görünüm de 1 (ort 1.0)
        # mean: 1 önce. max: 1 önce (2 vs 1). Fark 3 numarada görülür:
        # 3: görünümler 2 ve 0 → ortalama 1.0, maks 2
        merged = merge_views(self._rows([(1, 2), (2, 1), (3, 2)]),
                             self._rows([(1, 1), (2, 1), (3, 0)]))
        rank = {1: 1, 2: 2, 3: 3}
        assert ensemble_ordering(merged, "ads", raw_rank=rank,
                                 rank_rule="mean") == [1, 2, 3]
        assert ensemble_ordering(merged, "ads", raw_rank=rank,
                                 rank_rule="max")[:2] == [1, 3]

    def test_elimination_rule_identical_across_rules(self):
        # 9 yalnız TÜM görünümler 0 dediği için elenir; kural değişse de öyle
        merged = merge_views(self._rows([(1, 2), (9, 0)]),
                             self._rows([(1, 0), (9, 0)]))
        for rule in ("mean", "max", "any2"):
            order = ensemble_ordering(merged, "ads", rank_rule=rule)
            assert order[-1] == 9, f"{rule}: elenen kelime sonda olmalı"

    def test_any2_promotes_split_two_over_unanimous_one(self):
        """`any2`, ortalamanın eşitlediği (2,0) ile (1,1)'i ayırır.

        k2: iki görünüm de 1 → ortalama 1.0
        k3: görünümler 2 ve 0 → ortalama 1.0 AMA bir görünüm "2" dedi
        mean sıralaması ikisini ham skor rank'iyle ayırır; any2 k3'ü öne alır.
        Dijital verisinde bu bölünme nadir olduğu için iki kural aynı
        sonucu verdi — sözleşme farkı yine de test edilir.
        """
        merged = merge_views(self._rows([(2, 1), (3, 2)]),
                             self._rows([(2, 1), (3, 0)]))
        rank = {2: 1, 3: 2}   # ham skor k2'yi öne koyar
        assert ensemble_ordering(merged, "ads", raw_rank=rank,
                                 rank_rule="mean") == [2, 3]
        assert ensemble_ordering(merged, "ads", raw_rank=rank,
                                 rank_rule="any2") == [3, 2]


class TestDerivedManifestInheritance:
    """Codex 3. tur #1: türetilmiş artifact kaynak sözleşmesini miras alır."""

    def _sealed_source(self):
        m = build_manifest(provider="deepseek", model="m", seed=0,
                           dataset_slug="dijital", generated_at="T",
                           prompt_version=V3A, temperature=0.0, batch_size=10)
        return seal_artifact({"manifest": m, "report": {}})

    def test_inherits_actual_source_params(self):
        from app.core.screening.manifest import source_run_params

        params = source_run_params(self._sealed_source())
        assert params == {"prompt_version": V3A, "temperature": 0.0,
                          "batch_size": 10}
        derived = build_manifest(provider="offline", model="analysis", seed=-1,
                                 dataset_slug="dijital", generated_at="T",
                                 **params)
        assert derived["prompt_version"] == V3A
        assert derived["temperature"] == 0.0
        assert derived["batch_size"] == 10
        assert (derived["prompt_template_sha256"]
                == contract_hashes(V3A)["prompt_template_sha256"])

    def test_fails_closed_on_missing_source_fields(self):
        from app.core.screening.manifest import source_run_params

        with pytest.raises(ManifestError, match="manifest taşımıyor"):
            source_run_params({"report": {}})
        doc = self._sealed_source()
        doc["manifest"] = dict(doc["manifest"])
        del doc["manifest"]["temperature"]
        with pytest.raises(ManifestError, match="eksik"):
            source_run_params(doc)

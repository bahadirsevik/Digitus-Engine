# -*- coding: utf-8 -*-
"""Çoklu-bağlam ensemble birleştirme kuralları (Codex kilitli sözleşme).

Hiçbir ücretli çağrı yok — saf post-processing testleri.
"""
import pytest

from app.core.screening.ensemble import (
    ENSEMBLE_CONTRACT_VERSION,
    complementary_plans,
    ensemble_flip_rate,
    ensemble_ordering,
    ensemble_stats,
    merge_views,
)
from app.core.screening.runner import KeywordScreeningResult

CODES = {"ads": "AMBIGUOUS", "seo": "AMBIGUOUS", "social": "AMBIGUOUS"}


def _r(kid, ads, seo, social, unresolved=False):
    return KeywordScreeningResult(
        keyword_id=kid, keyword=f"k{kid}", ads_fit=ads, seo_fit=seo,
        social_fit=social, reason_codes=dict(CODES), unresolved=unresolved,
    )


def _kws(n):
    return [{"id": i, "keyword": f"kelime {i}"} for i in range(1, n + 1)]


class TestComplementaryPlans:
    def test_views_keep_position_but_change_neighbours(self):
        universe = _kws(50)
        a, b = complementary_plans(universe, 10, seed=42)
        assert [len(x) for x in a] == [len(x) for x in b]
        pos_a = {k["id"]: p for batch in a for p, k in enumerate(batch)}
        pos_b = {k["id"]: p for batch in b for p, k in enumerate(batch)}
        assert pos_a == pos_b                      # konum korunur
        mem_a = [sorted(k["id"] for k in x) for x in a]
        mem_b = [sorted(k["id"] for k in x) for x in b]
        assert mem_a != mem_b                      # komşular değişir
        assert sorted(k["id"] for x in b for k in x) == [k["id"] for k in universe]

    def test_plans_are_deterministic(self):
        u = _kws(40)
        a1, b1 = complementary_plans(u, 10, seed=7)
        a2, b2 = complementary_plans(u, 10, seed=7)
        assert [[k["id"] for k in x] for x in b1] == \
            [[k["id"] for k in x] for x in b2]


class TestMergeRules:
    def test_mean_fit_and_elimination_rule(self):
        a = [_r(1, 2, 1, 0), _r(2, 0, 0, 2), _r(3, 1, 2, 0)]
        b = [_r(1, 1, 1, 0), _r(2, 0, 2, 1), _r(3, 2, 0, 0)]
        m = {r.keyword_id: r for r in merge_views(a, b)}
        # Ortalama sıralama sinyali
        assert m[1].mean_fit["ads"] == 1.5
        assert m[3].mean_fit["seo"] == 1.0
        # ELEME yalnız İKİSİ de 0 derse
        assert m[2].passing["ads"] is False          # 0 ve 0 → elendi
        assert m[2].passing["seo"] is True           # 0 ve 2 → aday KALIR
        assert m[1].passing["social"] is False       # 0 ve 0
        assert m[3].passing["social"] is False
        # Ayrışma bayrağı
        assert m[1].context_disagreement["ads"] is True
        assert m[1].context_disagreement["seo"] is False
        # Ham kararlar saklanır
        assert m[1].raw_views["A"]["ads"] == 2 and m[1].raw_views["B"]["ads"] == 1

    def test_single_unresolved_uses_resolved_view(self):
        a = [_r(1, 2, 2, 2)]
        b = [_r(1, 1, 1, 1, unresolved=True)]
        m = merge_views(a, b)[0]
        assert m.unresolved_views == ["B"]
        assert m.uncertain is False
        assert m.mean_fit["ads"] == 2.0     # çözülmüş görünüm kullanıldı
        assert m.passing["ads"] is True

    def test_both_unresolved_stays_candidate_but_uncertain(self):
        a = [_r(1, 1, 1, 1, unresolved=True)]
        b = [_r(1, 1, 1, 1, unresolved=True)]
        m = merge_views(a, b)[0]
        assert m.uncertain is True
        assert all(m.passing[ch] for ch in ("ads", "seo", "social"))  # ELENMEZ
        assert m.mean_fit["ads"] is None      # yüksek öncelik almaz

    def test_views_must_cover_same_universe(self):
        with pytest.raises(ValueError, match="aynı evreni"):
            merge_views([_r(1, 1, 1, 1)], [_r(2, 1, 1, 1)])


class TestOrdering:
    def test_orders_by_mean_then_raw_rank(self):
        a = [_r(1, 2, 0, 0), _r(2, 2, 0, 0), _r(3, 1, 0, 0), _r(4, 0, 0, 0)]
        b = [_r(1, 1, 0, 0), _r(2, 2, 0, 0), _r(3, 1, 0, 0), _r(4, 0, 0, 0)]
        merged = merge_views(a, b)
        order = ensemble_ordering(merged, "ads",
                                  raw_rank={1: 4, 2: 3, 3: 2, 4: 1})
        # 2 (mean 2.0) > 1 (1.5) > 3 (1.0) > 4 (0.0, elendi → sonda)
        assert order == [2, 1, 3, 4]

    def test_uncertain_ranks_below_passing_above_eliminated(self):
        a = [_r(1, 2, 0, 0), _r(2, 1, 1, 1, unresolved=True), _r(3, 0, 0, 0)]
        b = [_r(1, 2, 0, 0), _r(2, 1, 1, 1, unresolved=True), _r(3, 0, 0, 0)]
        merged = merge_views(a, b)
        order = ensemble_ordering(merged, "ads")
        assert order == [1, 2, 3]  # geçen → belirsiz → elenen

    def test_raw_rank_breaks_mean_ties(self):
        """Tie-break sözleşmesi: relevance DEĞİL, ham skor rank'i (Codex #6)."""
        a = [_r(1, 2, 0, 0), _r(2, 2, 0, 0)]
        b = [_r(1, 2, 0, 0), _r(2, 2, 0, 0)]
        merged = merge_views(a, b)
        assert ensemble_ordering(merged, "ads",
                                 raw_rank={1: 9, 2: 1}) == [2, 1]


class TestEnsembleMetrics:
    def test_flip_rate_between_two_ensembles(self):
        e1 = merge_views([_r(1, 2, 1, 0), _r(2, 1, 1, 1)],
                         [_r(1, 2, 1, 0), _r(2, 1, 1, 1)])
        e2 = merge_views([_r(1, 2, 1, 0), _r(2, 0, 1, 1)],
                         [_r(1, 1, 1, 0), _r(2, 0, 1, 1)])
        flip = ensemble_flip_rate(e1, e2)
        assert flip["compared"] == 2
        # ADS: kelime 2 geçerken elendi (1,1 → 0,0)
        assert flip["per_channel"]["ADS"]["passing_flips"] == 1
        # Ortalama fit değişimi daha geniş (kelime 1: 2.0 → 1.5)
        assert flip["per_channel"]["ADS"]["mean_fit_flips"] == 2
        assert flip["any_channel_passing_flip_rate"] == 0.5

    def test_stats_report_disagreement_and_version(self):
        merged = merge_views([_r(1, 2, 1, 0), _r(2, 0, 0, 0)],
                             [_r(1, 1, 1, 0), _r(2, 0, 0, 0)])
        st = ensemble_stats(merged)
        assert st["ensemble_contract_version"] == ENSEMBLE_CONTRACT_VERSION
        assert st["ADS"]["context_disagreement"] == 1
        assert st["ADS"]["eliminated_both_zero"] == 1
        assert st["ADS"]["mean_fit_distribution"]["1.5"] == 1

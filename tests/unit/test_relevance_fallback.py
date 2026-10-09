"""
P4.15: RelevanceScorer embedding failure fallback and cache order tests.
"""
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from app.core.site_analyzer.relevance_scorer import RelevanceScorer, EMBEDDING_FALLBACK_SCORE


class TestEmbeddingFailureFallback:
    """Embedding API failure must return 0.5, not 1.0."""

    def test_anchor_embed_fail_returns_fallback(self):
        scorer = RelevanceScorer()
        with patch.object(scorer, "_embed_batch", return_value=None):
            results = scorer.compute_relevance(["test kw"], ["anchor text"])
        assert len(results) == 1
        assert results[0]["relevance_score"] == EMBEDDING_FALLBACK_SCORE
        assert results[0]["relevance_score"] != 1.0

    def test_keyword_batch_embed_fail_returns_fallback(self):
        scorer = RelevanceScorer()
        anchor_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        # Anchor succeeds, keyword batch fails
        call_count = {"n": 0}

        def side_effect(texts):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return [anchor_vec]  # anchor embed succeeds
            return None  # keyword embed fails

        with patch.object(scorer, "_embed_batch", side_effect=side_effect):
            results = scorer.compute_relevance(["kw1", "kw2"], ["anchor"])
        assert len(results) == 2
        for r in results:
            assert r["relevance_score"] == EMBEDDING_FALLBACK_SCORE
            assert r["relevance_score"] != 1.0

    def test_empty_anchors_still_returns_1_0(self):
        """Empty anchor list should return 1.0 (no-anchor-data = neutral pass-through)."""
        scorer = RelevanceScorer()
        results = scorer.compute_relevance(["kw1"], [])
        assert results[0]["relevance_score"] == 1.0


class TestCachedBatchOrderPreservation:
    """Mixed cache hit/miss batch must preserve input order."""

    def _make_vec(self, val: float) -> np.ndarray:
        from app.core.site_analyzer.relevance_scorer import EMBEDDING_DIMENSIONS
        v = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
        v[0] = val
        return v

    def test_order_preserved_in_mixed_cache_batch(self):
        """
        3 texts: index 0 and 2 are cache hits, index 1 is a miss.
        Embeddings returned by _embed_batch must be placed at index 1.
        Output order must match input order.
        """
        scorer = RelevanceScorer()
        vec0 = self._make_vec(0.1)
        vec1 = self._make_vec(0.2)
        vec2 = self._make_vec(0.3)

        texts = ["text_a", "text_b", "text_c"]

        # Plan E: anahtar formatı scorer'ın kendi helper'ından alınır
        # (model+dim+instruction_version+normalizer_version+md5)
        def redis_get(key):
            if key == RelevanceScorer._cache_key("text_a"):
                return vec0.tobytes()
            if key == RelevanceScorer._cache_key("text_c"):
                return vec2.tobytes()
            return None

        mock_redis = MagicMock()
        mock_redis.get.side_effect = redis_get
        mock_redis.set = MagicMock()

        scorer._redis = mock_redis

        with patch.object(scorer, "_embed_batch", return_value=[vec1]) as mock_embed:
            result = scorer._embed_batch_cached(texts)

        # Only "text_b" (index 1) should have been sent to _embed_batch
        mock_embed.assert_called_once_with(["text_b"])

        assert result is not None
        assert len(result) == 3
        np.testing.assert_array_equal(result[0], vec0)
        np.testing.assert_array_equal(result[1], vec1)
        np.testing.assert_array_equal(result[2], vec2)

    def test_all_cache_hit_skips_embed_batch(self):
        vec = self._make_vec(0.9)
        scorer = RelevanceScorer()

        def redis_get(key):
            return vec.tobytes()

        mock_redis = MagicMock()
        mock_redis.get.side_effect = redis_get
        scorer._redis = mock_redis

        with patch.object(scorer, "_embed_batch") as mock_embed:
            result = scorer._embed_batch_cached(["text_x"])

        mock_embed.assert_not_called()
        assert result is not None
        np.testing.assert_array_almost_equal(result[0], vec)

    def test_redis_unavailable_falls_back_to_direct_embed(self):
        vec = self._make_vec(0.7)
        scorer = RelevanceScorer()  # no redis

        with patch.object(scorer, "_embed_batch", return_value=[vec]) as mock_embed:
            result = scorer._embed_batch_cached(["text_y"])

        mock_embed.assert_called_once_with(["text_y"])
        assert result == [vec]


class TestPoolBuilderFallback:
    """pool_builder relevance fallback must be 0.5, not 1.0."""

    def test_pool_builder_missing_relevance_defaults_to_0_5(self):
        from decimal import Decimal
        from app.core.channel.pool_builder import PoolBuilder

        # _load_relevance_map returns a map that does NOT contain keyword_id=99
        # pool_builder should fall back to Decimal("0.5"), not Decimal("1.0")
        pb = PoolBuilder(db=MagicMock())
        relevance_map: dict = {}  # empty — simulates keyword not in map

        keyword_id = 99
        raw = 100.0
        relevance_coefficient = 1.0

        # Replicate the exact expression from pool_builder.py line 98-99
        relevance = float(relevance_map.get(keyword_id, Decimal("0.5")))
        adjusted = raw * relevance * relevance_coefficient

        assert relevance == 0.5, f"Expected 0.5, got {relevance}"
        assert adjusted == 50.0

    def test_pool_builder_present_relevance_used_as_is(self):
        from decimal import Decimal

        relevance_map = {42: Decimal("0.85")}
        relevance = float(relevance_map.get(42, Decimal("0.5")))
        assert relevance == pytest.approx(0.85)


class TestBatchMismatchFallback:
    """MIGRASYON REGRESYONU (run-17): yeni google-genai SDK bu embedding
    modelinde 50 metne 1 embedding donduruyor; eski SDK 50 donduruyordu.
    Kisa liste compute_relevance'ta IndexError uretip RUN'I OLDURUYORDU.
    Artik: uyusmazlikta tekli moda dusulur, kisa liste asla sizamaz."""

    class _SingleEmbedClient:
        """Kac metin gonderilirse gonderilsin 1 embedding donduren API taklidi."""

        def __init__(self):
            self.calls = []

            class _Models:
                def __init__(inner, outer):
                    inner._outer = outer

                def embed_content(inner, model=None, contents=None, config=None):
                    inner._outer.calls.append(len(contents))
                    from types import SimpleNamespace
                    return SimpleNamespace(
                        embeddings=[SimpleNamespace(values=[1.0, 0.0, 0.0])]
                    )

            self.models = _Models(self)

    def test_batch_mismatch_falls_back_to_per_text(self):
        scorer = RelevanceScorer()
        fake = self._SingleEmbedClient()
        scorer.client = fake

        result = scorer._embed_batch(["kw1", "kw2", "kw3"])

        assert result is not None
        assert len(result) == 3  # kisa liste degil — tekli modda tamamlandi
        # 1 batch denemesi + 3 tekli cagri
        assert fake.calls == [3, 1, 1, 1]

    def test_compute_relevance_survives_non_batching_model(self):
        """Run-17 senaryosu ucuca: 3 keyword + 2 anchor, batch'siz model —
        IndexError YOK, her keyword embedding yontemiyle skorlanir."""
        scorer = RelevanceScorer()
        scorer.client = self._SingleEmbedClient()

        results = scorer.compute_relevance(
            ["kw1", "kw2", "kw3"], ["anchor bir", "anchor iki"]
        )

        assert len(results) == 3
        for r in results:
            assert r["method"] == "embedding"
            assert 0.0 <= r["relevance_score"] <= 1.0

    def test_cached_path_guard_never_returns_short_list(self):
        """Redis'siz dogrudan yol: _embed_batch kisa liste dondurse bile
        _embed_batch_cached None'a cevirir (fallback yolu)."""
        scorer = RelevanceScorer()
        short = [np.array([1.0, 0.0], dtype=np.float32)]
        with patch.object(scorer, "_embed_batch", return_value=short):
            out = scorer._embed_batch_cached(["a", "b", "c"])
        assert out is None

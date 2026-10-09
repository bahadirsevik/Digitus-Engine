# -*- coding: utf-8 -*-
"""v2.1 kanal stratejisi çekirdek testleri (plan Faz C)."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.policy.channel_strategy import (
    StrategyValidationError,
    canonical_channel_strategy_fingerprint,
    draft_strategy_suggestion,
    normalize_strategy_payload,
    strategy_snapshot_for_run,
)


class TestFingerprint:
    def test_whitespace_insensitive(self):
        a = canonical_channel_strategy_fingerprint({
            "product_definition": "Beyaz  saç çözümü ",
            "content_strategy": "ürün-problem alanının tamamı",
            "social_mode": "hype",
        })
        b = canonical_channel_strategy_fingerprint({
            "product_definition": " Beyaz saç   çözümü",
            "content_strategy": "ürün-problem alanının  tamamı",
            "social_mode": "HYPE",
        })
        assert a == b

    def test_semantic_change_alters_fingerprint(self):
        base = {"product_definition": "a", "content_strategy": "b",
                "social_mode": "hype"}
        assert canonical_channel_strategy_fingerprint(base) != (
            canonical_channel_strategy_fingerprint({**base,
                                                    "social_mode": "authority"})
        )
        assert canonical_channel_strategy_fingerprint(base) != (
            canonical_channel_strategy_fingerprint({**base,
                                                    "content_strategy": "c"})
        )

    def test_invalid_social_mode_rejected(self):
        with pytest.raises(StrategyValidationError, match="social_mode"):
            normalize_strategy_payload("a", "b", "viral")


class TestSnapshot:
    def test_draft_is_not_approved(self):
        ws = SimpleNamespace(
            channel_strategy={"product_definition": "x",
                              "content_strategy": "y",
                              "social_mode": "hype", "status": "draft"},
            strategy_version=0,
        )
        assert strategy_snapshot_for_run(ws) is None

    def test_approved_snapshot_carries_version_and_fingerprint(self):
        strategy = {"product_definition": "x", "content_strategy": "y",
                    "social_mode": "hype", "schema_version": 1,
                    "status": "approved"}
        ws = SimpleNamespace(channel_strategy=strategy, strategy_version=3)
        snap = strategy_snapshot_for_run(ws)
        assert snap["strategy_version"] == 3
        assert snap["social_mode"] == "hype"
        assert snap["fingerprint"] == (
            canonical_channel_strategy_fingerprint(strategy)
        )


class TestDraftSuggestion:
    def test_prefills_from_profile_and_defaults_hype(self):
        ws = SimpleNamespace(profile_data={
            "brand_summary": "Beyaz saçları eski rengine döndüren kozmetik.",
            "products": ["GR-7 losyon"],
            "problems_solved": ["beyaz saç"],
        })
        draft = draft_strategy_suggestion(ws)
        assert draft["status"] == "draft"
        assert draft["social_mode"] == "hype"
        assert "kozmetik" in draft["product_definition"]
        assert draft["content_strategy"] == ""  # kullanıcı beyanı ŞART


class TestStrategyStaleFreshness:
    """Plan test listesi: v2 etkilenmez; v2_1 NULL/farklı → stale, eşit → fresh."""

    def _run(self, algo, pool_strategy_version):
        return SimpleNamespace(
            channel_pool_policy_version=1,
            relevance_anchor_version=1,
            skip_relevance=True,
            algorithm_version=algo,
            channel_pool_strategy_version=pool_strategy_version,
        )

    def _ws(self, strategy_version):
        return SimpleNamespace(policy_version=1, anchor_version=1,
                               strategy_version=strategy_version)

    def test_v2_run_never_strategy_stale(self):
        from app.core.policy.freshness import compute_pool_freshness

        f = compute_pool_freshness(self._run("v2", None), self._ws(5))
        assert not f.strategy_stale and not f.channel_pool_stale

    def test_v21_null_pool_version_is_stale(self):
        from app.core.policy.freshness import compute_pool_freshness

        f = compute_pool_freshness(self._run("v2_1", None), self._ws(1))
        assert f.strategy_stale and f.channel_pool_stale

    def test_v21_equal_fresh_mismatch_stale(self):
        from app.core.policy.freshness import compute_pool_freshness

        assert not compute_pool_freshness(
            self._run("v2_1", 2), self._ws(2)
        ).strategy_stale
        assert compute_pool_freshness(
            self._run("v2_1", 2), self._ws(3)
        ).strategy_stale

    def test_v3_run_never_strategy_stale(self):
        from app.core.engine.context import build_firm_profile, firm_block, firm_block_sha256
        from app.core.policy.freshness import compute_pool_freshness

        run = self._run("v3", None)
        ws = SimpleNamespace(
            policy_version=1,
            anchor_version=1,
            strategy_version=5,
            status="confirmed",
            deleted_at=None,
            profile_data={"company_name": "TestCorp"},
        )
        sha = firm_block_sha256(firm_block(build_firm_profile(ws)))
        run.execution_manifest = {"engine_v3": {"firm_block_sha256": sha}}
        f = compute_pool_freshness(run, ws)
        assert f.strategy_stale is False
        assert f.channel_pool_stale is False

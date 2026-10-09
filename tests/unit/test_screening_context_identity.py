# -*- coding: utf-8 -*-
"""Faz 1 temel sözleşmeleri: kanonik bağlam + iki ayrı kimlik + config.

Plan §3 (tek bağlam üreticisi), §5.6 (iki kimlik), §6.2 (config otoritesi).
Bu testler screening'in üretim yoluna girmeden ÖNCE reuse/drift
semantiğini kilitler.
"""
import json
from types import SimpleNamespace

import pytest

from app.core.screening.candidate_union import (
    PRODUCTION_SCREENING_CONTRACT,
    SCREENING_APPLIED_CHANNELS_V3,
    UNION_CONTRACT_V3,
)
from app.core.screening.context import (
    CONTEXT_CONTRACT_VERSION,
    PLACEHOLDER,
    ScreeningContextMissing,
    canonical_screening_context,
    screening_context_from_snapshot,
)
from app.core.screening.identity import (
    IDENTITY_CONTRACT_VERSION,
    SCREENING_RUNNER_CONTRACT_VERSION,
    screening_runner_contract,
    ScreeningIdentityError,
    candidate_materialization_identity,
    channel_rank_snapshot_sha256,
    relevance_rows_sha256,
    screening_input_identity,
    universe_sha256,
)

STRATEGY_OK = {
    "product_definition": "Borsa verisi ve hisse analiz platformu",
    "content_strategy": "Yatırımcıya karar destek içerikleri",
    "social_mode": "hype",
    "schema_version": 1,
    "status": "approved",
    "fingerprint": "f" * 16,
}


def _ws(strategy=None, profile=None, version=3):
    return SimpleNamespace(
        channel_strategy=(strategy if strategy is not None
                          else dict(STRATEGY_OK)),
        strategy_version=version,
        profile_data=(profile if profile is not None
                      else {"target_audience": "Bireysel yatırımcı"}),
    )


class TestCanonicalContext:
    def test_context_fields_and_sha(self, monkeypatch):
        import app.core.policy.channel_strategy as cs

        monkeypatch.setattr(cs, "approved_strategy",
                            lambda ws: dict(STRATEGY_OK))
        ctx = canonical_screening_context(_ws())
        assert ctx["contract_version"] == CONTEXT_CONTRACT_VERSION
        assert set(ctx["fields"]) == {"product_definition",
                                      "content_strategy", "social_mode",
                                      "target_audience"}
        assert ctx["fields"]["target_audience"] == "Bireysel yatırımcı"
        assert len(ctx["context_sha256"]) == 64
        # kanonik payload: sort_keys + kompakt separator
        assert ctx["canonical_payload"].startswith('{"content_strategy":')
        assert ", " not in ctx["canonical_payload"]

    def test_missing_strategy_fails_closed(self, monkeypatch):
        import app.core.policy.channel_strategy as cs

        monkeypatch.setattr(cs, "approved_strategy", lambda ws: None)
        with pytest.raises(ScreeningContextMissing, match="STRATEGY_REQUIRED"):
            canonical_screening_context(_ws())

    def test_empty_required_field_fails_closed(self, monkeypatch):
        import app.core.policy.channel_strategy as cs

        bad = dict(STRATEGY_OK, content_strategy="   ")
        monkeypatch.setattr(cs, "approved_strategy", lambda ws: bad)
        with pytest.raises(ScreeningContextMissing, match="content_strategy"):
            canonical_screening_context(_ws())

    def test_none_workspace_fails_closed(self):
        with pytest.raises(ScreeningContextMissing):
            canonical_screening_context(None)

    def test_missing_audience_uses_placeholder_and_changes_hash(
            self, monkeypatch):
        import app.core.policy.channel_strategy as cs

        monkeypatch.setattr(cs, "approved_strategy",
                            lambda ws: dict(STRATEGY_OK))
        without = canonical_screening_context(_ws(profile={}))
        with_aud = canonical_screening_context(_ws())
        assert without["fields"]["target_audience"] == PLACEHOLDER
        assert without["target_audience_present"] is False
        assert with_aud["target_audience_present"] is True
        # hedef kitle sonradan doldurulursa bağlam DEĞİŞİR (reuse düşer)
        assert without["context_sha256"] != with_aud["context_sha256"]

    def test_invisible_differences_do_not_change_hash(self, monkeypatch):
        """CRLF/NBSP/unicode form farkı reuse'u boşuna düşürmemeli."""
        import app.core.policy.channel_strategy as cs

        base = dict(STRATEGY_OK, content_strategy="Satır1\nSatır2")
        noisy = dict(STRATEGY_OK,
                     content_strategy="  Satır1\r\nSatır2  ")
        monkeypatch.setattr(cs, "approved_strategy", lambda ws: base)
        a = canonical_screening_context(_ws())
        monkeypatch.setattr(cs, "approved_strategy", lambda ws: noisy)
        b = canonical_screening_context(_ws())
        assert a["context_sha256"] == b["context_sha256"]

    def test_meaningful_change_changes_hash(self, monkeypatch):
        import app.core.policy.channel_strategy as cs

        monkeypatch.setattr(cs, "approved_strategy",
                            lambda ws: dict(STRATEGY_OK))
        a = canonical_screening_context(_ws())
        monkeypatch.setattr(
            cs, "approved_strategy",
            lambda ws: dict(STRATEGY_OK, social_mode="authority"))
        b = canonical_screening_context(_ws())
        assert a["context_sha256"] != b["context_sha256"]

    def test_snapshot_reproduces_same_hash(self, monkeypatch):
        """Worker canlı profili OKUMAZ; snapshot AYNI hash'i üretir."""
        import app.core.policy.channel_strategy as cs

        monkeypatch.setattr(cs, "approved_strategy",
                            lambda ws: dict(STRATEGY_OK))
        live = canonical_screening_context(_ws())
        snap = screening_context_from_snapshot(dict(live["fields"]))
        assert snap["context_sha256"] == live["context_sha256"]
        assert snap["canonical_payload"] == live["canonical_payload"]

    def test_snapshot_missing_field_fails_closed(self):
        with pytest.raises(ScreeningContextMissing, match="social_mode"):
            screening_context_from_snapshot(
                {"product_definition": "x", "content_strategy": "y"})
        with pytest.raises(ScreeningContextMissing, match="dict"):
            screening_context_from_snapshot("metin")


class TestUniverseHash:
    def test_order_independent_but_content_sensitive(self):
        rows_a = [{"keyword_id": 2, "text": "b"}, {"keyword_id": 1, "text": "a"}]
        rows_b = [{"keyword_id": 1, "text": "a"}, {"keyword_id": 2, "text": "b"}]
        assert universe_sha256(rows_a) == universe_sha256(rows_b)
        rows_c = [{"keyword_id": 1, "text": "a"}, {"keyword_id": 2, "text": "B"}]
        assert universe_sha256(rows_a) != universe_sha256(rows_c)

    def test_invalid_rows_fail_closed(self):
        with pytest.raises(ScreeningIdentityError, match="evren boş"):
            universe_sha256([])
        with pytest.raises(ScreeningIdentityError, match="keyword_id"):
            universe_sha256([{"keyword_id": "1", "text": "a"}])
        with pytest.raises(ScreeningIdentityError, match="tekrarlanan"):
            universe_sha256([{"keyword_id": 1, "text": "a"},
                             {"keyword_id": 1, "text": "b"}])
        with pytest.raises(ScreeningIdentityError, match="metin boş"):
            universe_sha256([{"keyword_id": 1, "text": ""}])


class TestScreeningInputIdentity:
    def _kwargs(self, **over):
        kwargs = dict(
            scoring_run_id=24,
            universe_rows=[{"keyword_id": 1, "text": "a"},
                           {"keyword_id": 2, "text": "b"}],
            context_sha256="c" * 64,
        )
        kwargs.update(over)
        return kwargs

    def test_stable_and_documents_components(self):
        a = screening_input_identity(**self._kwargs())
        b = screening_input_identity(**self._kwargs())
        assert a["sha256"] == b["sha256"]
        comp = a["components"]
        assert comp["identity_contract_version"] == IDENTITY_CONTRACT_VERSION
        assert comp["union_contract_version"] == UNION_CONTRACT_V3
        assert comp["applied_screening_channels"] == ["ADS", "SEO"]
        assert comp["model"] == PRODUCTION_SCREENING_CONTRACT["model"]
        assert comp["view_salts"] == list(
            PRODUCTION_SCREENING_CONTRACT["view_salts"])

    def test_context_change_breaks_reuse(self):
        a = screening_input_identity(**self._kwargs())
        b = screening_input_identity(**self._kwargs(context_sha256="d" * 64))
        assert a["sha256"] != b["sha256"]

    def test_universe_change_breaks_reuse(self):
        a = screening_input_identity(**self._kwargs())
        b = screening_input_identity(**self._kwargs(
            universe_rows=[{"keyword_id": 1, "text": "a"},
                           {"keyword_id": 3, "text": "c"}]))
        assert a["sha256"] != b["sha256"]

    def test_scope_change_breaks_reuse(self):
        """Kapsam (SOCIAL dahil edilirse) reuse'u DÜŞÜRMELİ."""
        a = screening_input_identity(**self._kwargs())
        b = screening_input_identity(**self._kwargs(
            applied_screening_channels=("ADS", "SEO", "SOCIAL")))
        assert a["sha256"] != b["sha256"]

    def test_prompt_or_model_drift_breaks_reuse(self):
        a = screening_input_identity(**self._kwargs())
        drifted = dict(PRODUCTION_SCREENING_CONTRACT,
                       prompt_version="SCR-2026-08-01-v4")
        b = screening_input_identity(**self._kwargs(contract=drifted))
        assert a["sha256"] != b["sha256"]
        drifted2 = dict(PRODUCTION_SCREENING_CONTRACT, batch_size=30)
        c = screening_input_identity(**self._kwargs(contract=drifted2))
        assert a["sha256"] != c["sha256"]

    def test_run_id_and_bad_inputs_fail_closed(self):
        with pytest.raises(ScreeningIdentityError, match="scoring_run_id"):
            screening_input_identity(**self._kwargs(scoring_run_id=0))
        with pytest.raises(ScreeningIdentityError, match="context_sha256"):
            screening_input_identity(**self._kwargs(context_sha256="abc"))
        with pytest.raises(ScreeningIdentityError,
                           match="applied_screening_channels"):
            screening_input_identity(**self._kwargs(
                applied_screening_channels=()))


class TestMaterializationIdentity:
    def _kwargs(self, **over):
        kwargs = dict(
            scoring_run_id=24,
            screening_input_identity_sha256="a" * 64,
            channel_rank_snapshot_sha256="b" * 64,
            relevance_rows_sha256="c" * 64,
            relevance_anchor_version=4,
            relevance_coefficient=1.0,
            active_channels=("ADS", "SEO", "SOCIAL"),
            capacities={"ADS": 17, "SEO": 33, "SOCIAL": 20},
            budgets={"ADS": {"B": 51, "T": 153, "U": 174},
                     "SEO": {"B": 60, "T": 180, "U": 202},
                     "SOCIAL": {"B": 60, "T": 60, "U": 60}},
            algorithm_version="v2",
            policy_version=7,
            strategy_version=3,
            assignment_version=2,
        )
        kwargs.update(over)
        return kwargs

    def test_stable_and_screening_free_variant(self):
        a = candidate_materialization_identity(**self._kwargs())
        assert a["sha256"] == candidate_materialization_identity(
            **self._kwargs())["sha256"]
        off = candidate_materialization_identity(
            **self._kwargs(screening_input_identity_sha256=None))
        assert off["sha256"] != a["sha256"]
        assert off["components"]["screening_input_identity_sha256"] is None

    def test_relevance_or_capacity_change_rematerializes(self):
        base = candidate_materialization_identity(**self._kwargs())
        for over in ({"relevance_coefficient": 0.8},
                     {"relevance_rows_sha256": "d" * 64},
                     {"relevance_anchor_version": 5},
                     {"capacities": {"ADS": 17, "SEO": 40, "SOCIAL": 20}},
                     {"active_channels": ("ADS", "SEO")},
                     {"algorithm_version": "v2_1"}):
            kwargs = self._kwargs(**over)
            if "active_channels" in over:
                kwargs["capacities"] = {"ADS": 17, "SEO": 33}
                kwargs["budgets"] = {
                    "ADS": {"B": 51, "T": 153, "U": 174},
                    "SEO": {"B": 60, "T": 180, "U": 202}}
            other = candidate_materialization_identity(**kwargs)
            assert other["sha256"] != base["sha256"], over

    def test_budget_change_rematerializes(self):
        base = candidate_materialization_identity(**self._kwargs())
        budgets = {"ADS": {"B": 51, "T": 153, "U": 175},
                   "SEO": {"B": 60, "T": 180, "U": 202},
                   "SOCIAL": {"B": 60, "T": 60, "U": 60}}
        other = candidate_materialization_identity(
            **self._kwargs(budgets=budgets))
        assert other["sha256"] != base["sha256"]

    def test_scope_change_rematerializes(self):
        base = candidate_materialization_identity(**self._kwargs())
        other = candidate_materialization_identity(**self._kwargs(
            applied_screening_channels=("ADS", "SEO", "SOCIAL")))
        assert other["sha256"] != base["sha256"]

    def test_missing_capacity_or_budget_fails_closed(self):
        with pytest.raises(ScreeningIdentityError, match="kapasite eksik"):
            candidate_materialization_identity(
                **self._kwargs(capacities={"ADS": 17, "SEO": 33}))
        bad_budgets = {"ADS": {"B": 51, "T": 153}, "SEO": {},
                       "SOCIAL": {"B": 60, "T": 60, "U": 60}}
        with pytest.raises(ScreeningIdentityError, match="bütçesinde"):
            candidate_materialization_identity(
                **self._kwargs(budgets=bad_budgets))

    def test_short_sha_fails_closed(self):
        with pytest.raises(ScreeningIdentityError, match="64"):
            candidate_materialization_identity(
                **self._kwargs(channel_rank_snapshot_sha256="abc"))


class TestSnapshotHashHelpers:
    def test_rank_snapshot_is_order_independent(self):
        a = channel_rank_snapshot_sha256(
            {"ADS": {2: 2, 1: 1}, "SEO": {1: 5}})
        b = channel_rank_snapshot_sha256(
            {"SEO": {1: 5}, "ADS": {1: 1, 2: 2}})
        assert a == b
        c = channel_rank_snapshot_sha256(
            {"ADS": {1: 1, 2: 3}, "SEO": {1: 5}})
        assert a != c

    def test_relevance_none_is_distinct_from_zero(self):
        assert (relevance_rows_sha256({1: None})
                != relevance_rows_sha256({1: 0.0}))

    def test_empty_rank_snapshot_fails_closed(self):
        with pytest.raises(ScreeningIdentityError, match="boş"):
            channel_rank_snapshot_sha256({})


class TestConfigAuthority:
    def test_defaults_are_flag_off_and_v3_contract(self):
        """Varsayilan KAPALI olmali.

        Canli `.env` (dev makinesinde bayrak acik olabilir) test hukmunu
        DEGISTIRMEMELI: siniftaki alan varsayilani dogrulanir.
        """
        from app.config import Settings

        assert Settings.model_fields[
            "ENABLE_CORPUS_SCREENING"].default is False
        assert SCREENING_APPLIED_CHANNELS_V3 == ("ADS", "SEO")

    def test_global_mode_is_ui_default_only(self):
        """Plan §6.2: global mode gerçek run/attempt kararını EZMEZ."""
        from app.config import Settings

        s = Settings()
        assert not hasattr(s, "CORPUS_SCREENING_MODE")
        assert s.CORPUS_SCREENING_UI_DEFAULT_MODE == "off"

    def test_flag_on_requires_positive_finite_caps(self):
        from app.config import Settings

        with pytest.raises(ValueError, match="CORPUS_SCREENING_MAX_APPROVED"):
            Settings(ENABLE_CORPUS_SCREENING=True,
                     CORPUS_SCREENING_MAX_APPROVED_USD=0)
        with pytest.raises(ValueError, match="CORPUS_DOWNSTREAM_MAX_APPROVED"):
            Settings(ENABLE_CORPUS_SCREENING=True,
                     CORPUS_DOWNSTREAM_MAX_APPROVED_USD=float("inf"))
        with pytest.raises(ValueError, match="MAX_KEYWORDS"):
            Settings(ENABLE_CORPUS_SCREENING=True,
                     CORPUS_SCREENING_MAX_KEYWORDS=0)
        # bayrak kapalıyken guard çalışmaz (mevcut kurulumları bozmaz)
        assert Settings(ENABLE_CORPUS_SCREENING=False,
                        CORPUS_SCREENING_MAX_APPROVED_USD=0) is not None

    def test_flag_on_with_valid_caps_boots(self):
        from app.config import Settings

        s = Settings(ENABLE_CORPUS_SCREENING=True,
                     CORPUS_SCREENING_MAX_APPROVED_USD=0.5,
                     CORPUS_DOWNSTREAM_MAX_APPROVED_USD=8.0)
        assert s.ENABLE_CORPUS_SCREENING is True

    def test_assistive_canary_allowlist_is_limited_to_two_workspaces(self):
        from app.config import Settings

        with pytest.raises(ValueError, match="en fazla 2 workspace"):
            Settings(
                ENABLE_CORPUS_SCREENING=True,
                CORPUS_SCREENING_DEFAULT_MODE="assistive",
                CORPUS_SCREENING_ALLOWLIST=[1, 2, 3],
                CORPUS_SCREENING_MAX_APPROVED_USD=8.0,
                CORPUS_DOWNSTREAM_MAX_APPROVED_USD=8.0,
            )


class TestCodexRound5Fixes:
    """Kimlik anahtarları tablolardan ÖNCE kapatıldı (Codex 5. tur)."""

    def _mat(self, **over):
        kwargs = dict(
            scoring_run_id=24,
            screening_input_identity_sha256=None,     # off yolu
            channel_rank_snapshot_sha256="b" * 64,
            relevance_rows_sha256=None,
            relevance_anchor_version=None,
            relevance_coefficient=1.0,
            active_channels=("ADS", "SEO"),
            capacities={"ADS": 17, "SEO": 33},
            budgets={"ADS": {"B": 51, "T": 51, "U": 51},
                     "SEO": {"B": 60, "T": 60, "U": 60}},
            algorithm_version="v2",
            policy_version=None,
            strategy_version=None,
            assignment_version=None,
        )
        kwargs.update(over)
        return candidate_materialization_identity(**kwargs)

    # #1 — off yolunda run kimliği kaybolmamalı
    def test_off_path_different_runs_have_different_identity(self):
        a = self._mat(scoring_run_id=24)
        b = self._mat(scoring_run_id=25)
        assert a["sha256"] != b["sha256"]
        assert a["components"]["scoring_run_id"] == 24

    def test_materialization_requires_valid_run_id(self):
        for bad in (0, -1, True, "24", None):
            with pytest.raises(ScreeningIdentityError, match="scoring_run_id"):
                self._mat(scoring_run_id=bad)

    # #2 — kanal listeleri kanonik sıralı
    def test_channel_order_does_not_change_identity(self):
        a = self._mat(active_channels=("ADS", "SEO"))
        b = self._mat(active_channels=("SEO", "ADS"))
        assert a["sha256"] == b["sha256"]
        assert a["components"]["active_channels"] == ["ADS", "SEO"]

    def test_scope_order_does_not_change_screening_identity(self):
        base = dict(scoring_run_id=24,
                    universe_rows=[{"keyword_id": 1, "text": "a"}],
                    context_sha256="c" * 64)
        a = screening_input_identity(
            **base, applied_screening_channels=("ADS", "SEO"))
        b = screening_input_identity(
            **base, applied_screening_channels=("SEO", "ADS"))
        assert a["sha256"] == b["sha256"]
        assert a["components"]["applied_screening_channels"] == ["ADS", "SEO"]

    # #3 — runner davranışı kimliğe bağlı
    def test_runner_contract_is_part_of_identity(self):
        comp = screening_input_identity(
            scoring_run_id=24,
            universe_rows=[{"keyword_id": 1, "text": "a"}],
            context_sha256="c" * 64)["components"]
        rc = comp["runner_contract"]
        assert rc["runner_contract_version"] == SCREENING_RUNNER_CONTRACT_VERSION
        for key in ("max_output_tokens", "max_transient_retries",
                    "max_parse_retries", "max_missing_retries",
                    "single_retry_limit", "unresolved_fallback_fit"):
            assert key in rc

    def test_runner_contract_drift_breaks_reuse(self):
        base = dict(scoring_run_id=24,
                    universe_rows=[{"keyword_id": 1, "text": "a"}],
                    context_sha256="c" * 64)
        a = screening_input_identity(**base)
        drifted = dict(screening_runner_contract(), max_output_tokens=4096)
        b = screening_input_identity(**base, runner_contract=drifted)
        assert a["sha256"] != b["sha256"]
        drifted2 = dict(screening_runner_contract(), single_retry_limit=3)
        c = screening_input_identity(**base, runner_contract=drifted2)
        assert a["sha256"] != c["sha256"]

    def test_runner_contract_reads_live_constants(self):
        from app.core.screening.contract import MAX_OUTPUT_TOKENS
        from app.core.screening.runner import (
            SINGLE_RETRY_LIMIT,
            UNRESOLVED_FALLBACK_FIT,
        )

        rc = screening_runner_contract()
        assert rc["max_output_tokens"] == MAX_OUTPUT_TOKENS
        assert rc["single_retry_limit"] == SINGLE_RETRY_LIMIT
        assert rc["unresolved_fallback_fit"] == UNRESOLVED_FALLBACK_FIT

    # #4 — fingerprint doğru anahtardan
    def test_fingerprint_read_from_approved_snapshot(self, monkeypatch):
        import app.core.policy.channel_strategy as cs

        approved = {**STRATEGY_OK, "approved_fingerprint": "abc123"}
        approved.pop("fingerprint", None)
        monkeypatch.setattr(cs, "approved_strategy", lambda ws: approved)
        ctx = canonical_screening_context(_ws())
        assert ctx["strategy_fingerprint"] == "abc123"

    # #5 — SHA hex doğrulaması + config guard'ları
    def test_non_hex_sha_rejected(self):
        with pytest.raises(ScreeningIdentityError, match="hex"):
            self._mat(channel_rank_snapshot_sha256="z" * 64)
        with pytest.raises(ScreeningIdentityError, match="hex"):
            screening_input_identity(
                scoring_run_id=1,
                universe_rows=[{"keyword_id": 1, "text": "a"}],
                context_sha256="Z" * 64)

    def test_config_guards_cover_runtime_fields(self):
        from app.config import Settings

        common = dict(ENABLE_CORPUS_SCREENING=True,
                      CORPUS_SCREENING_MAX_APPROVED_USD=0.5,
                      CORPUS_DOWNSTREAM_MAX_APPROVED_USD=8.0)
        with pytest.raises(ValueError, match="CONCURRENCY"):
            Settings(**common, CORPUS_SCREENING_CONCURRENCY=0)
        with pytest.raises(ValueError, match="UI_DEFAULT_MODE"):
            Settings(**common, CORPUS_SCREENING_UI_DEFAULT_MODE="hayalet")
        with pytest.raises(ValueError, match="PROVIDER"):
            Settings(**common, CORPUS_SCREENING_PROVIDER="openai")
        with pytest.raises(ValueError, match="MODEL"):
            Settings(**common, CORPUS_SCREENING_MODEL="   ")

"""SOCIAL v4 prompt sozlesmesi — muhurlu degerlere BIREBIR baglilik."""
from __future__ import annotations

import pytest

from app.core.screening.candidate_union import PRODUCTION_SCREENING_CONTRACT
from app.core.screening.social_v4_contract import (
    SOCIAL_SCREENING_CONTRACT_V4 as C,
    identity_fields,
    prompt_template_sha256,
    response_schema_sha256,
    verify_sealed_contract,
)

SEALED_TPL = "df7a5edfae995f47d99a899459401cfe6397ab1789348048c56d7920f0d78895"
SEALED_SCHEMA = "4dffa950ce8a8718e72d9d07916c3e49a52dc955d6ec03c3542e81b1a30524ab"


class TestSealedValues:

    def test_pins_match_the_sealed_experiment(self):
        assert C["prompt_template_sha256"] == SEALED_TPL
        assert C["response_schema_sha256"] == SEALED_SCHEMA
        assert C["batch_size"] == 30
        assert C["temperature"] == 0.0
        assert C["views"] == 2

    def test_live_files_still_reproduce_the_seal(self):
        v = verify_sealed_contract()
        assert v["prompt_template_matches"] is True
        assert v["response_schema_matches"] is True

    def test_schema_canonicalization_is_pinned(self):
        """separators EKLENIRSE muhurlu deger TUTMAZ — hata tekrarlamasin."""
        import hashlib
        import json

        from app.core.screening import social_prompt as sp
        wrong = hashlib.sha256(json.dumps(
            sp.RESPONSE_SCHEMA, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest()
        assert wrong != SEALED_SCHEMA
        assert response_schema_sha256() == SEALED_SCHEMA
        assert C["schema_canonicalization"] == "json.dumps(schema, sort_keys=True)"

    def test_prompt_template_helper_matches_source(self):
        assert prompt_template_sha256() == SEALED_TPL


class TestAdsSeoPathUntouched:

    def test_production_tri_channel_contract_unchanged(self):
        assert PRODUCTION_SCREENING_CONTRACT["batch_size"] == 10
        assert PRODUCTION_SCREENING_CONTRACT["prompt_version"] == "SCR-2026-07-27-v3a"
        assert (PRODUCTION_SCREENING_CONTRACT["prompt_template_sha256"]
                == "1ac634926f4987f69cb66f7ad58f943619e9289bc4a2bd60d0b58b7b3a0cbe03")

    def test_social_contract_is_channel_scoped(self):
        assert C["channel"] == "SOCIAL"
        assert C["batch_size"] != PRODUCTION_SCREENING_CONTRACT["batch_size"]


class TestIdentityAndAuthority:

    def test_identity_carries_all_reuse_fields(self):
        from app.core.screening.cache_eligibility import REQUIRED_FIELDS
        f = identity_fields(workspace_id=38, strategy_fingerprint="fp",
                            universe_sha256="uni",
                            union_contract_version="UNION-v4")
        assert set(REQUIRED_FIELDS) <= set(f), (
            "cache reuse alanlarinin tamami identity'de olmali")

    def test_deepseek_is_retrieval_only(self):
        assert C["deepseek_authority"] == "retrieval_only_no_final_keep_reject"

    @pytest.mark.parametrize("field", [
        "patron_labels", "baseline_rank", "monthly_volume", "social_score",
        "current_selection", "capacity_K"])
    def test_forbidden_prompt_inputs_declared(self, field):
        assert field in C["forbidden_prompt_inputs"]

    def test_forbidden_fields_absent_from_prompt_context(self):
        from app.core.screening import social_prompt as sp
        allowed = set(sp.ALLOWED_CONTEXT_KEYS)
        for bad in C["forbidden_prompt_inputs"]:
            assert bad not in allowed

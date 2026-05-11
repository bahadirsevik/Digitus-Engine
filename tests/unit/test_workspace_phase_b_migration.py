"""Phase B normalizer compatibility test.

NOTE (plan2 P0/C1 squash): The Phase B migration file was archived into
`migrations/versions/_archived/` when the migration chain was squashed
(commit "P0/C1 — Docker test infra + migration smoke + chain squash").
We reload it from the archive so the normalizer's edge-case behaviour is
still pinned, since the runtime normalizer (`app.core.keyword_normalize`)
must stay backward-compatible with whatever the backfill produced.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_phase_b_module():
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / "migrations" / "versions" / "20260508_001_workspace_phase_b_backfill.py",
        root / "migrations" / "versions" / "_archived" / "20260508_001_workspace_phase_b_backfill.py",
    ]
    path = next((c for c in candidates if c.exists()), None)
    if path is None:
        pytest.skip(
            "Phase B migration file not found (neither active nor archived); "
            "test obsolete after squash."
        )
    spec = importlib.util.spec_from_file_location("workspace_phase_b_backfill", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_phase_b_normalizer_matches_runtime_edge_cases():
    module = _load_phase_b_module()

    assert module._normalize_keyword_v1("İSTANBUL") == "istanbul"
    assert module._normalize_keyword_v1("FÖN TARAĞI") == "fon taragi"
    assert module._normalize_keyword_v1("  saç    bakım   ") == "sac bakim"
    assert module._normalize_keyword_v1(None) == ""

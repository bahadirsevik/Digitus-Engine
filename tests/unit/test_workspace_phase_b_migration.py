import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_phase_b_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "migrations" / "versions" / "20260508_001_workspace_phase_b_backfill.py"
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

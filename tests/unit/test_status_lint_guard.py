import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.check_scoring_status_assignments import find_violations


def test_no_direct_scoring_run_status_assignments():
    assert find_violations() == []

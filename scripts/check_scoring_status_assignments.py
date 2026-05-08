"""Guard against direct ScoringRun.status assignments.

State changes for ScoringRun should go through app.core.scoring.state_machine.
This intentionally allows BrandProfile/TaskResult status assignments.
"""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
IGNORED_PARTS = {
    ("core", "scoring", "state_machine.py"),
}
PATTERNS = [
    re.compile(r"\bscoring_run\.status\s*="),
    re.compile(r"\brun\.status\s*="),
    re.compile(r"\bdb_run\.status\s*="),
]


def _ignored(path: Path) -> bool:
    rel = path.relative_to(APP)
    parts = tuple(rel.parts)
    return parts in IGNORED_PARTS


def find_violations() -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    for path in APP.rglob("*.py"):
        if _ignored(path):
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                violations.append((path, lineno, line.strip()))
    return violations


def main() -> int:
    violations = find_violations()
    if not violations:
        print("OK: no direct ScoringRun.status assignments found")
        return 0

    print("Direct ScoringRun.status assignments found:")
    for path, lineno, line in violations:
        print(f"- {path.relative_to(ROOT)}:{lineno}: {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

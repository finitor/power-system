"""Tiny golden-file ("approval") helper for whole-frame render tests.

Rendered terminal frames are layout — constantly under human scrutiny on the
live console — so asserting them line-by-line is brittle and high-churn (a label
rename or column tweak breaks a dozen assertions). Instead we snapshot the whole
frame to a committed golden file and compare. A deliberate layout change is then
re-blessed in one command rather than hand-edited:

    UPDATE_GOLDEN=1 python -m pytest tests/test_api_terminal_display.py

Comparison is whitespace-normalized at line ends so terminal-width padding
(e.g. the header's ljust to the detected column count) doesn't make the golden
environment-dependent.
"""

from __future__ import annotations

import os
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def check_golden(test, name: str, actual: str) -> None:
    """Compare `actual` to tests/golden/<name>.txt, or rewrite it when UPDATE_GOLDEN is set."""
    path = GOLDEN_DIR / f"{name}.txt"
    normalized = _normalize(actual)
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized + "\n", encoding="utf-8")
        return
    if not path.exists():
        test.fail(f"missing golden file {path}; create it with UPDATE_GOLDEN=1")
    expected = path.read_text(encoding="utf-8").rstrip("\n")
    test.assertEqual(
        normalized,
        expected,
        f"\n{name} differs from its golden file ({path}). "
        "If the change is intended, re-bless with UPDATE_GOLDEN=1.\n",
    )

#!/usr/bin/env python3
"""Compatibility wrapper for the packaged supervisor display command."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from offgrid_power.cli.supervisor_display import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

"""Persistence for operator runtime overrides that must survive a restart.

These are knobs an operator sets in flight (currently just the CCL scaling
factor) that should outlive a supervisor restart. They live in a small JSON
file the supervisor owns — deliberately *not* the hand-edited env config — so a
machine writer never clobbers comments or boot configuration. The env var
remains the boot default and is consulted only when the JSON has no value.

Reads are defensive: a missing file, unreadable file, malformed JSON, or an
out-of-range value all read as "no override" so a corrupt file degrades to the
configured default rather than wedging startup.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile

from .charge_ceiling import MAX_CCL_SCALING_FACTOR, MIN_CCL_SCALING_FACTOR

logger = logging.getLogger(__name__)

CCL_SCALING_FACTOR_KEY = "ccl_scaling_factor"


def load_ccl_scaling_factor(path: str | os.PathLike[str] | None) -> float | None:
    """Return the persisted CCL scaling factor, or None to fall back to env/default."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        value = float(data[CCL_SCALING_FACTOR_KEY])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("Ignoring runtime state at %s: %s", path, exc)
        return None
    if not (MIN_CCL_SCALING_FACTOR <= value <= MAX_CCL_SCALING_FACTOR):
        logger.warning("Ignoring out-of-range persisted CCL scaling factor %.3f at %s", value, path)
        return None
    return round(value, 4)


def save_ccl_scaling_factor(path: str | os.PathLike[str] | None, value: float) -> None:
    """Persist the CCL scaling factor atomically; never raise into the caller.

    Writes to a temp file in the same directory and renames over the target so a
    crash mid-write can't leave a half-written state file. A failure to persist
    is logged, not raised — the in-memory value is still in effect.
    """
    if not path:
        return
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".runtime-state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({CCL_SCALING_FACTOR_KEY: round(value, 4)}, handle)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError as exc:
        logger.warning("Could not persist runtime state to %s: %s", path, exc)

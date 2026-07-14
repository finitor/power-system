"""Persistence for operator runtime overrides that must survive a restart.

These are knobs an operator sets in flight (CCL scaling and charge-controller
operational switches) that should outlive a supervisor restart. They live in a small JSON
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
from threading import Lock

from .charge_ceiling import MAX_CCL_SCALING_FACTOR, MIN_CCL_SCALING_FACTOR

logger = logging.getLogger(__name__)

CCL_SCALING_FACTOR_KEY = "ccl_scaling_factor"
CHARGE_CONTROLLER_ENABLED_KEY = "charge_controller_enabled"

_state_lock = Lock()


def _load_state(path: str | os.PathLike[str] | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Ignoring runtime state at %s: %s", path, exc)
        return {}


def _save_state(path: str | os.PathLike[str] | None, update) -> None:
    """Atomically update one part of the shared runtime-state document."""
    if not path:
        return
    target = Path(path)
    with _state_lock:
        try:
            data = _load_state(path)
            update(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".runtime-state-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, sort_keys=True)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as exc:
            logger.warning("Could not persist runtime state to %s: %s", path, exc)


def load_ccl_scaling_factor(path: str | os.PathLike[str] | None) -> float | None:
    """Return the persisted CCL scaling factor, or None to fall back to env/default."""
    if not path:
        return None
    data = _load_state(path)
    if CCL_SCALING_FACTOR_KEY not in data:
        return None
    try:
        value = float(data[CCL_SCALING_FACTOR_KEY])
    except (ValueError, TypeError, KeyError) as exc:
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
    _save_state(path, lambda data: data.__setitem__(CCL_SCALING_FACTOR_KEY, round(value, 4)))


def load_charge_controller_enabled(path: str | os.PathLike[str] | None) -> dict[int, bool]:
    """Return explicitly persisted controller switches; missing entries default on."""
    raw = _load_state(path).get(CHARGE_CONTROLLER_ENABLED_KEY, {})
    if not isinstance(raw, dict):
        logger.warning("Ignoring invalid persisted charge-controller state at %s", path)
        return {}
    result: dict[int, bool] = {}
    for index in (0, 1):
        value = raw.get(str(index))
        if isinstance(value, bool):
            result[index] = value
    return result


def save_charge_controller_enabled(
    path: str | os.PathLike[str] | None,
    enabled: dict[int, bool],
) -> None:
    """Persist both controller operational switches without losing other overrides."""
    payload = {str(index): bool(enabled.get(index, True)) for index in (0, 1)}
    _save_state(path, lambda data: data.__setitem__(CHARGE_CONTROLLER_ENABLED_KEY, payload))

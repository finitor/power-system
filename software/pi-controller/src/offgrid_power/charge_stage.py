"""Canonical charge-stage vocabulary shared across charge controllers.

The MidNite Classic and EPEver TEP report charge stages with different
words. We normalize both to one industry-standard vocabulary (Classic's,
which is the baseline) so internal coordination logic and the displays
speak a single language across controllers.

Note on EPEver: it collapses the constant-current (bulk) and
constant-voltage (absorb) phases into one "Boost" status -- it has no
separate bulk stage. We map Boost -> ABSORB (its literal meaning is the
elevated absorption-voltage stage); an EPEver reporting ABSORB may
therefore still be physically in the bulk current-limited climb.

Not every canonical stage is supported by every vendor, and that is fine:
each controller's map only emits the values it can reach. HYPERVOC (the
Classic's PV-overvoltage self-protection) is Classic-only -- the EPEver map
never produces it -- and it is kept distinct from RESTING so the protection
state is observable rather than hidden behind ordinary "not charging".
"""

from __future__ import annotations

from enum import Enum


class ChargeStage(str, Enum):
    """Canonical charge stage. str-valued so it renders directly."""

    BULK = "Bulk"
    ABSORB = "Absorb"
    FLOAT = "Float"
    EQUALIZE = "Equalize"
    RESTING = "Resting"
    HYPERVOC = "HyperVoc"
    UNKNOWN = "Unknown"


# MidNite Classic native stage string -> canonical.
_CLASSIC_MAP = {
    "BulkMppt": ChargeStage.BULK,
    "Absorb": ChargeStage.ABSORB,
    "Float": ChargeStage.FLOAT,
    "FloatMppt": ChargeStage.FLOAT,
    "Equalize": ChargeStage.EQUALIZE,
    "Resting": ChargeStage.RESTING,
    # PV-overvoltage self-protection: not charging, but kept distinct from
    # RESTING so the state is observable. Classic-only.
    "HyperVoc": ChargeStage.HYPERVOC,
}

# EPEver native charging-status string -> canonical.
_EPEVER_MAP = {
    "No charging": ChargeStage.RESTING,
    "Boost": ChargeStage.ABSORB,
    "Float": ChargeStage.FLOAT,
    "Equalize": ChargeStage.EQUALIZE,
}


def normalize_classic_stage(native: str | None) -> ChargeStage:
    if native is None:
        return ChargeStage.UNKNOWN
    return _CLASSIC_MAP.get(native, ChargeStage.UNKNOWN)


def normalize_epever_stage(native: str | None) -> ChargeStage:
    if native is None:
        return ChargeStage.UNKNOWN
    return _EPEVER_MAP.get(native, ChargeStage.UNKNOWN)

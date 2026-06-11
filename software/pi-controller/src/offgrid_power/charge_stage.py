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

from dataclasses import dataclass
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


@dataclass(frozen=True)
class NormalizedStage:
    """A charge stage carrying both its canonical name and, when it adds
    information, the controller's native word.

    This is the unit serialized into API data blocks and handed to renderers,
    so renderers never need vendor-specific knowledge: they display the
    canonical name and, only if ``vendor`` is present, the native word in
    parentheses.
    """

    canonical: str
    vendor: str | None  # set only when the native word differs from canonical

    def as_dict(self) -> dict[str, str | None]:
        return {"canonical": self.canonical, "vendor": self.vendor}

    @classmethod
    def from_dict(cls, data: dict | None) -> "NormalizedStage":
        if not data:
            return cls(canonical=ChargeStage.UNKNOWN.value, vendor=None)
        return cls(canonical=data.get("canonical") or ChargeStage.UNKNOWN.value, vendor=data.get("vendor"))

    def render(self, state: str | None = None) -> str:
        text = f"Stage: {self.canonical}"
        if self.vendor:
            text += f" ({self.vendor})"
        if state and state not in (self.canonical, self.vendor):
            text += f"  State: {state}"
        return text


def _normalized(canonical: ChargeStage, native: str | None) -> NormalizedStage:
    vendor = native if native and native != canonical.value else None
    return NormalizedStage(canonical=canonical.value, vendor=vendor)


def classic_stage(native: str | None) -> NormalizedStage:
    return _normalized(normalize_classic_stage(native), native)


def epever_stage(native: str | None) -> NormalizedStage:
    return _normalized(normalize_epever_stage(native), native)

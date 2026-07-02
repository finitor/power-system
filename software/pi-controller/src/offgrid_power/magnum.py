"""Magnum Energy inverter/charger RS-485 telemetry client."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from magnum_pi.bus import MagnumBus
from magnum_pi.models.inverter import InverterPacket
from magnum_pi.models.remote import RemotePacket

from .metrics import TelemetryEvent

log = logging.getLogger(__name__)

# MS4448PAE is a 48V system; voltage bytes are 12V-nominal × 10 on the wire.
VOLTAGE_MULTIPLIER = 4
# Model byte identifying this inverter in the extended packet at position 14.
_INVERTER_MODEL_BYTE = 0x73
# Packet header bytes belonging to non-remote, non-inverter accessories.
_ACCESSORY_HEADERS = {0x81, 0x91, 0xA1, 0xA2}


@dataclass(frozen=True)
class MagnumSnapshot:
    """Decoded state from one Magnum bus cycle."""

    captured_at: datetime
    # From inverter packet
    dc_volts: float
    dc_amps: int
    ac_volts_out: int
    ac_volts_in: int
    ac_amps_in: int | None
    ac_amps_out: int | None
    ac_freq_hz: float | None
    inverter_on: bool
    charger_on: bool
    status_name: str       # InverterStatus enum member name
    fault_name: str        # InverterFault enum member name
    battery_temp_c: int
    transformer_temp_c: int
    fet_temp_c: int
    # From remote packet (None if remote not seen in this cycle)
    absorb_v: float | None = None
    float_v: float | None = None
    absorb_time_hr: float | None = None
    shore_amps: int | None = None
    charger_amps_pct: int | None = None

    @property
    def dc_power_w(self) -> int:
        return round(self.dc_volts * self.dc_amps)

    @property
    def ac_power_w(self) -> int | None:
        if self.ac_amps_out is None:
            return None
        return self.ac_volts_out * self.ac_amps_out

    def status_label(self) -> str:
        """Human-readable operating state derived from status and LEDs."""
        name = self.status_name
        labels = {
            "INVERT": "Inverting",
            "SEARCH": "Search mode",
            "CHARGE": "Charging",
            "BULK": "Charging (bulk)",
            "ABSORB": "Charging (absorb)",
            "FLOAT": "Charging (float)",
            "EQ": "Equalizing",
            "BATSAVER": "Battery saver",
            "STANDBY": "Standby",
            "INVERTER_STANDBY": "Inverter standby",
            "OFF": "Off",
        }
        return labels.get(name, name.lower().replace("_", " "))

    def fault_label(self) -> str | None:
        """Fault description, or None if no fault."""
        if self.fault_name in ("NONE", "UNKNOWN"):
            return None
        return self.fault_name.lower().replace("_", " ")


def _find_packets(raw_packets: list) -> tuple[bytes | None, bytes | None]:
    """Return (inverter_bytes, remote_bytes) from a cycle's raw packets.

    The magnum-pi CycleTracker can misidentify packets when joining the bus
    mid-cycle. We detect the actual inverter packet by its model byte (0x73 =
    MS4448PAE) at position 14, independent of the tracker's classification.
    The remote is the other 21/22-byte non-accessory packet.
    """
    inverter: bytes | None = None
    remote: bytes | None = None
    for _, data in raw_packets:
        if len(data) < 21:
            continue
        if data[0] in _ACCESSORY_HEADERS:
            continue
        if data[14] == _INVERTER_MODEL_BYTE:
            inverter = data
        elif remote is None:
            remote = data
    return inverter, remote


def _snapshot_from_cycle(raw_packets: list) -> MagnumSnapshot | None:
    inverter_data, remote_data = _find_packets(raw_packets)
    if inverter_data is None:
        return None

    try:
        inv = InverterPacket.from_bytes(inverter_data)
    except Exception as exc:
        log.debug("Failed to parse inverter packet: %s", exc)
        return None

    remote: RemotePacket | None = None
    if remote_data is not None:
        try:
            remote = RemotePacket.from_bytes(remote_data, voltage_multiplier=VOLTAGE_MULTIPLIER)
        except Exception as exc:
            log.debug("Failed to parse remote packet: %s", exc)

    absorb_v: float | None = None
    float_v: float | None = None
    absorb_time_hr: float | None = None
    shore_amps: int | None = None
    charger_amps_pct: int | None = None

    if remote is not None:
        base = remote.base
        absorb_v = base.custom_absorb_v  # None if battery_type encoding instead
        float_v = base.float_v if base.float_v > 0 else None
        absorb_time_hr = base.absorb_time_hr if base.absorb_time_hr > 0 else None
        shore_amps = base.shore_amps
        charger_amps_pct = base.charger_amps_pct

    return MagnumSnapshot(
        captured_at=datetime.now(timezone.utc),
        dc_volts=inv.dc_volts,
        dc_amps=inv.dc_amps,
        ac_volts_out=inv.ac_volts_out,
        ac_volts_in=inv.ac_volts_in,
        ac_amps_in=inv.ac_amps_in,
        ac_amps_out=inv.ac_amps_out,
        ac_freq_hz=inv.ac_freq_hz,
        inverter_on=inv.inverter_led,
        charger_on=inv.charger_led,
        status_name=inv.status.name,
        fault_name=inv.fault.name,
        battery_temp_c=inv.battery_temp_c,
        transformer_temp_c=inv.transformer_temp_c,
        fet_temp_c=inv.fet_temp_c,
        absorb_v=absorb_v,
        float_v=float_v,
        absorb_time_hr=absorb_time_hr,
        shore_amps=shore_amps,
        charger_amps_pct=charger_amps_pct,
    )


class MagnumClient:
    """Synchronous Magnum bus reader.

    Each call to read() opens the serial port, waits for a cycle that
    contains an identifiable MS4448PAE inverter packet, and returns.
    Uses asyncio.run() internally; safe to call from synchronous code.
    """

    # Wait long enough to join a valid bus cycle, but fail promptly now that the
    # Magnum tap is on a clean CH340 interface. A failing read can take up to
    # ~max_cycles*0.5 s, but it runs on the Magnum actor thread so it never
    # stalls the main poll tick.
    # The remote packet (charge setpoints, shore/charger limits) is not present
    # in every bus cycle, so these fields are frequently None on an otherwise
    # healthy read. Carry forward the last seen values so consumers (e.g. the
    # display's Charge Settings row) stay stable instead of blinking out for a
    # cycle and strobing everything below them.
    _SETTINGS_FIELDS = ("absorb_v", "float_v", "absorb_time_hr", "shore_amps", "charger_amps_pct")

    def __init__(self, device: str, max_cycles: int = 10) -> None:
        self._device = device
        self._max_cycles = max_cycles
        self._last_settings: dict[str, float | int] = {}
        self._pending_settings: dict[str, float | int] | None = None

    def read(self) -> MagnumSnapshot | None:
        if self._device and not os.path.exists(self._device):
            # A missing serial node means the adapter is unplugged/absent.
            # Surface it as an error (mirroring the EPEver's "Could not open …")
            # so the supervisor records it and health reporting classifies it as
            # transport_absent, rather than swallowing it as "no data". Failures
            # after a successful open stay best-effort (return None): a silent
            # bus is no-data, not an adapter fault.
            raise ConnectionError(f"Could not open {self._device}")
        try:
            snapshot = asyncio.run(self._read_async())
        except Exception as exc:
            log.warning("Magnum read failed: %s", exc)
            return None
        return self._merge_last_settings(snapshot) if snapshot is not None else None

    def _merge_last_settings(self, snapshot: MagnumSnapshot) -> MagnumSnapshot:
        """Fill settings from a confirmed cache, ignoring one-off remote decodes.

        The Magnum remote packet is intermittent, and on a noisy/framing-imperfect
        tap an occasional parseable-but-wrong remote decode can make static charge
        settings appear to jump. Require a newly decoded settings tuple to repeat
        before accepting it as the value to display and persist.
        """
        decoded = self._settings_from_snapshot(snapshot)
        if decoded:
            if decoded == self._last_settings or decoded == self._pending_settings:
                self._last_settings = decoded
                self._pending_settings = None
            else:
                self._pending_settings = decoded

        fill: dict[str, float | int | None] = {}
        for field in self._SETTINGS_FIELDS:
            fill[field] = self._last_settings.get(field)
        return replace(snapshot, **fill)

    def _settings_from_snapshot(self, snapshot: MagnumSnapshot) -> dict[str, float | int]:
        return {
            field: value
            for field in self._SETTINGS_FIELDS
            if (value := getattr(snapshot, field)) is not None
        }

    async def _read_async(self) -> MagnumSnapshot | None:
        async with MagnumBus(self._device) as bus:
            for _ in range(self._max_cycles):
                try:
                    cycle = await asyncio.wait_for(bus.read_cycle(), timeout=0.5)
                except asyncio.TimeoutError:
                    log.debug("Magnum bus cycle timeout")
                    continue
                snapshot = _snapshot_from_cycle(cycle.raw_packets)
                if snapshot is not None:
                    return snapshot
        log.warning("Magnum: no valid inverter packet seen in %d cycles", self._max_cycles)
        return None


# Inverter faults that indicate the inverter shut off for a battery reason
# rather than a manual toggle or AC transfer.
_LOW_BATTERY_FAULTS = {"LOW_BAT", "DEAD_BAT"}


class InverterEventTracker:
    """Detects inverter on->off transitions and classifies cut-outs.

    A transition to OFF carrying a low-battery fault is an LBCO cut-out —
    the event worth capturing for autonomy analysis. An off-transition with
    no such fault (manual toggle, AC transfer) is logged as a plain
    inverter_off so the record is complete and the two are distinguishable.
    """

    def __init__(self) -> None:
        self._was_inverting: bool | None = None

    def observe(self, magnum: MagnumSnapshot | None, battery=None) -> TelemetryEvent | None:
        if magnum is None:
            return None
        inverting = magnum.inverter_on
        previous = self._was_inverting
        self._was_inverting = inverting
        if previous is None or previous == inverting:
            return None
        if inverting:
            event = "inverter_on"
        elif magnum.fault_name in _LOW_BATTERY_FAULTS:
            event = "lbco_cutout"
        else:
            event = "inverter_off"

        soc = voltage = None
        if battery is not None:
            if getattr(battery, "state_of_charge", None) is not None:
                soc = battery.state_of_charge.soc_percent
            if getattr(battery, "measurements", None) is not None:
                voltage = battery.measurements.voltage_v
        return TelemetryEvent(
            captured_at=magnum.captured_at,
            source="magnum",
            event=event,
            detail={
                "fault": magnum.fault_name,
                "dc_volts": magnum.dc_volts,
                "battery_soc_percent": soc,
                "battery_voltage_v": voltage,
            },
        )

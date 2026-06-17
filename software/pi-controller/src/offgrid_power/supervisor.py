"""Read-only supervisory snapshot assembly."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import TYPE_CHECKING

from .ambient import AmbientDhtClient, AmbientDs18b20Client, AmbientProbeDisconnected, AmbientTelemetry
from .canbus import BatteryCanClient, CanBusHealth, PylonCanSnapshot, canbus_health
from .classic import ClassicChargeSettings, ClassicClient, ClassicTelemetry
from .epever import EpeverChargeSettings, EpeverClient, EpeverTelemetry
from .readers import DeviceReading, PollingReader

if TYPE_CHECKING:
    from .magnum import MagnumClient, MagnumSnapshot


STATUS_OK = "OK"
STATUS_WARNING = "WARNING"
STATUS_ERROR = "ERROR"

CELL_HIGH_VOLTAGE_WARNING_V = 3.55
CELL_OVERVOLTAGE_ALERT_V = 3.60
CELL_DELTA_TOP_OF_CHARGE_V = 3.45
CELL_DELTA_WARNING_MV = 75
CELL_DELTA_CRITICAL_MV = 100


@dataclass(frozen=True)
class SupervisorSnapshot:
    captured_at: datetime
    classic: ClassicTelemetry | None
    classic_settings: ClassicChargeSettings | None
    epever: EpeverTelemetry | None
    epever_settings: EpeverChargeSettings | None
    battery: PylonCanSnapshot | None
    battery_can_health: CanBusHealth | None
    ambient: AmbientTelemetry | None
    magnum: MagnumSnapshot | None
    errors: list[str]
    status_conditions: list[str] = field(default_factory=list)
    status_severity: str = STATUS_OK
    # Devices with no adapter configured at all, so no read is attempted. Lets
    # health reporting say "disabled" rather than "offline" (which implies a
    # read was tried and returned nothing).
    disabled_devices: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.status_conditions and self.status_severity == STATUS_OK:
            object.__setattr__(self, "status_severity", STATUS_WARNING)

    @property
    def status_text(self) -> str:
        # A device read failure means that one device is offline. As long as the
        # supervisor is still assembling snapshots, that is a degraded (WARNING)
        # state, not a supervisor-level ERROR — restarting the supervisor would
        # not bring the device back. ERROR is reserved for critical analyzed
        # conditions (e.g. battery overvoltage) raised via status_severity.
        if self.status_severity == STATUS_ERROR:
            return STATUS_ERROR
        if self.errors or self.status_severity == STATUS_WARNING:
            return STATUS_WARNING
        return STATUS_OK

    @property
    def ok(self) -> bool:
        # "ok" means not in a hard-error (down) state. A degraded WARNING — an
        # offline device or a non-critical condition — is still ok; only a
        # critical ERROR is not.
        return self.status_text != STATUS_ERROR


@dataclass(frozen=True)
class StatusConditionCandidate:
    key: str
    text: str
    required_samples: int = 1
    severity: str = STATUS_WARNING


AmbientClient = AmbientDhtClient | AmbientDs18b20Client


class Supervisor:
    def __init__(
        self,
        classic: ClassicClient | None,
        ambient: AmbientClient | None = None,
        battery: BatteryCanClient | None = None,
        battery_can_interface: str | None = None,
        magnum: MagnumClient | None = None,
        epever: EpeverClient | None = None,
    ) -> None:
        self.classic = classic
        self.ambient = ambient
        self.battery = battery
        self.battery_can_interface = battery_can_interface
        self.magnum = magnum
        self.epever = epever
        self._status_condition_counts: dict[str, int] = {}
        self._readers: dict[str, PollingReader] | None = None

    def start_readers(self, interval_s: float = 5.0, expire_after_s: float | None = None) -> None:
        """Switch to per-device actor threads.

        Each configured adapter is owned by one thread: it polls on an
        interval, and writes reach the device by being submitted to the same
        thread (see write_classic_charge_settings), so reads and writes to a
        device can never race. read_snapshot composes from the cached
        last-good values without blocking on any device.
        """
        if self._readers is not None:
            return
        readers: dict[str, PollingReader] = {}
        if self.classic is not None:
            readers["classic"] = PollingReader(
                "classic", self.classic.read, interval_s, expire_after_s=expire_after_s
            )
        if self.epever is not None:
            readers["epever"] = PollingReader(
                "epever", self.epever.read, interval_s, expire_after_s=expire_after_s
            )
        if self.battery is not None:
            readers["battery"] = PollingReader(
                "battery",
                lambda: validated_battery_snapshot(self.battery.read()),
                interval_s,
                expire_after_s=expire_after_s,
            )
        if self.ambient is not None:
            readers["ambient"] = PollingReader("ambient", self.ambient.read, interval_s)
        if self.magnum is not None:
            readers["magnum"] = PollingReader(
                "magnum", self.magnum.read, interval_s, expire_after_s=expire_after_s
            )
        for reader in readers.values():
            reader.start()
        self._readers = readers

    def stop_readers(self) -> None:
        if self._readers is None:
            return
        for reader in self._readers.values():
            reader.stop()
        self._readers = None

    def wait_for_initial_readings(self, timeout_s: float = 10.0) -> None:
        """Block until every reader has either a value or an error.

        Used once at startup so the first snapshot isn't empty while the
        actor threads complete their first polls.
        """
        if self._readers is None:
            return
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            readings = [reader.reading() for reader in self._readers.values()]
            if all(reading.captured_at is not None or reading.error is not None for reading in readings):
                return
            time.sleep(0.2)

    def request_refresh(self) -> None:
        """Queue an out-of-cycle poll of every device; returns immediately.

        Fire-and-forget so a slow source can never block the caller (e.g. an
        HTTP request triggered by a manual panel switch). The fresh values are
        picked up by the next read_snapshot(). A no-op when readers are not
        running, since read_snapshot then polls every device directly anyway.
        """
        if self._readers is None:
            return
        for reader in self._readers.values():
            reader.request_refresh()

    def write_classic_charge_settings(self, **kwargs):
        """Write Classic charge settings via the device's actor thread.

        All Classic I/O must go through one thread; when readers are active
        the write executes on the classic reader thread between polls, so it
        can never race a read on the shared Modbus connection.
        """
        if self.classic is None:
            raise RuntimeError("no Classic adapter configured")

        def write():
            return self.classic.write_charge_settings(**kwargs)

        if self._readers is not None and "classic" in self._readers:
            return self._readers["classic"].submit(write)
        return write()

    def write_epever_charge_voltages(self, **kwargs) -> EpeverChargeSettings:
        """Write EPEver charge voltages via the device actor thread."""
        if self.epever is None:
            raise RuntimeError("no EPEver adapter configured")

        def write() -> EpeverChargeSettings:
            return self.epever.write_charge_voltages(**kwargs)

        if self._readers is not None and "epever" in self._readers:
            return self._readers["epever"].submit(write)  # type: ignore[no-any-return]
        return write()

    def write_epever_max_charging_current(self, current_a: float) -> EpeverChargeSettings:
        """Write EPEver BAT Max Charging Current via the device actor thread."""
        if self.epever is None:
            raise RuntimeError("no EPEver adapter configured")

        def write() -> EpeverChargeSettings:
            return self.epever.write_max_charging_current(current_a)

        if self._readers is not None and "epever" in self._readers:
            return self._readers["epever"].submit(write)  # type: ignore[no-any-return]
        return write()

    def write_epever_charge_times(self, **kwargs) -> EpeverChargeSettings:
        """Write EPEver charge-stage timers via the device actor thread."""
        if self.epever is None:
            raise RuntimeError("no EPEver adapter configured")

        def write() -> EpeverChargeSettings:
            return self.epever.write_charge_times(**kwargs)

        if self._readers is not None and "epever" in self._readers:
            return self._readers["epever"].submit(write)  # type: ignore[no-any-return]
        return write()

    def set_epever_charging(self, enabled: bool) -> bool:
        """Write EPEver charge-enable coil via the device actor thread."""
        if self.epever is None:
            raise RuntimeError("no EPEver adapter configured")

        def write() -> bool:
            return self.epever.set_charging(enabled)

        if self._readers is not None and "epever" in self._readers:
            return self._readers["epever"].submit(write)  # type: ignore[no-any-return]
        return write()

    def write_magnum_charge_settings(self, **kwargs) -> None:
        """Placeholder for future Magnum charge-setting writes.

        The Magnum telemetry tap is TX-capable at the library level, but this
        codebase does not yet have a verified, read-modify-write charge-setting
        primitive that preserves the active remote packet. Keep the supervisor
        API stable while refusing the unsafe operation explicitly.
        """
        if self.magnum is None:
            raise RuntimeError("no Magnum adapter configured")
        raise NotImplementedError("Magnum charge-setting writes are not implemented")

    def read_snapshot(self) -> SupervisorSnapshot:
        if self._readers is not None:
            devices, errors, stale_candidates = self._collect_from_readers()
        else:
            devices, errors, stale_candidates = self._collect_direct()

        battery_can_health: CanBusHealth | None = None
        if self.battery_can_interface is not None:
            battery_can_health = canbus_health(self.battery_can_interface)
            if not battery_can_health.ok:
                errors.append(battery_can_health.status_message())

        classic_settings = devices["classic_settings"]
        battery = devices["battery"]

        status_condition_candidates = charge_limit_status_condition_candidates(classic_settings, battery)
        status_condition_candidates.extend(self._stable_status_condition_candidates(cell_status_condition_candidates(battery)))
        status_condition_candidates.extend(stale_candidates)
        status_conditions = [candidate.text for candidate in status_condition_candidates]
        status_severity = status_condition_severity(status_condition_candidates)

        return SupervisorSnapshot(
            captured_at=datetime.now(timezone.utc),
            classic=devices["classic"],
            classic_settings=classic_settings,
            epever=devices["epever"],
            epever_settings=devices["epever_settings"],
            battery=battery,
            battery_can_health=battery_can_health,
            ambient=devices["ambient"],
            magnum=devices["magnum"],
            errors=errors,
            status_conditions=status_conditions,
            status_severity=status_severity,
            disabled_devices=self._disabled_devices(),
        )

    def _disabled_devices(self) -> frozenset[str]:
        return frozenset(
            name
            for name, client in (
                ("classic", self.classic),
                ("epever", self.epever),
                ("battery", self.battery),
                ("magnum", self.magnum),
                ("ambient", self.ambient),
            )
            if client is None
        )

    def _collect_direct(self) -> tuple[dict, list[str], list[StatusConditionCandidate]]:
        errors: list[str] = []
        devices: dict = {
            "classic": None,
            "classic_settings": None,
            "epever": None,
            "epever_settings": None,
            "battery": None,
            "ambient": None,
            "magnum": None,
        }

        if self.classic is not None:
            try:
                devices["classic"], devices["classic_settings"] = self.classic.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Classic read failed: {exc}")

        if self.epever is not None:
            try:
                devices["epever"], devices["epever_settings"] = self.epever.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"EPEver read failed: {exc}")

        if self.ambient is not None:
            try:
                devices["ambient"] = self.ambient.read()
            except AmbientProbeDisconnected:
                pass
            except Exception:  # noqa: BLE001 - ambient is advisory unless a control loop depends on it.
                pass

        if self.battery is not None:
            try:
                devices["battery"] = self.battery.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Battery CAN read failed: {exc}")

        if self.magnum is not None:
            try:
                devices["magnum"] = self.magnum.read()
            except Exception as exc:  # noqa: BLE001 - supervisor should show adapter errors.
                errors.append(f"Magnum read failed: {exc}")

        return devices, errors, []

    # Error message prefixes match the direct-read path so displays and
    # alerting behave identically in both modes.
    _READER_ERROR_PREFIXES = {
        "classic": "Classic read failed",
        "epever": "EPEver read failed",
        "battery": "Battery CAN read failed",
        "magnum": "Magnum read failed",
    }
    _READER_LABELS = {
        "classic": "Charge controller",
        "epever": "EPEver charge controller",
        "battery": "Battery CAN",
        "magnum": "Magnum inverter",
    }

    def _collect_from_readers(self) -> tuple[dict, list[str], list[StatusConditionCandidate]]:
        assert self._readers is not None
        now = datetime.now(timezone.utc)
        errors: list[str] = []
        stale_candidates: list[StatusConditionCandidate] = []
        devices: dict = {
            "classic": None,
            "classic_settings": None,
            "epever": None,
            "epever_settings": None,
            "battery": None,
            "ambient": None,
            "magnum": None,
        }

        for name, reader in self._readers.items():
            reading = reader.reading()

            if name == "ambient":
                # Ambient keeps direct-path semantics: a failed probe means
                # "no reading", never a stale carried-over temperature.
                if reading.error is None and reading.value is not None:
                    devices["ambient"] = reading.value
                continue

            # Past the expiry, the cached value is too old to show: leave the
            # device None so the displays render "No data". Staleness below
            # still warns while the last-good value is shown in the grace window.
            if reading.value is not None and not reading.is_expired(now):
                if name == "classic":
                    devices["classic"], devices["classic_settings"] = reading.value
                elif name == "epever":
                    devices["epever"], devices["epever_settings"] = reading.value
                else:
                    devices[name] = reading.value

            if reading.error is not None:
                errors.append(f"{self._READER_ERROR_PREFIXES[name]}: {reading.error}")

            if reading.is_stale(now):
                age = reading.age_seconds(now)
                stale_candidates.append(
                    StatusConditionCandidate(
                        f"reader.{name}.stale",
                        f"{self._READER_LABELS[name]} telemetry stale: last good read {age:.0f}s ago",
                    )
                )

        return devices, errors, stale_candidates

    def _stable_status_condition_candidates(self, candidates: list[StatusConditionCandidate]) -> list[StatusConditionCandidate]:
        active_keys = {candidate.key for candidate in candidates}
        self._status_condition_counts = {
            key: count
            for key, count in self._status_condition_counts.items()
            if key in active_keys
        }

        conditions: list[str] = []
        for candidate in candidates:
            count = self._status_condition_counts.get(candidate.key, 0) + 1
            self._status_condition_counts[candidate.key] = count
            if count >= candidate.required_samples:
                conditions.append(candidate)
        return conditions


def validated_battery_snapshot(snapshot: PylonCanSnapshot) -> PylonCanSnapshot:
    """Reject partial CAN reads so they cannot overwrite a good cache.

    When the bus is flapping, a 1.5s collection window can catch a sparse
    frame burst and decode a snapshot with no measurements frame. Caching
    that as "last good" makes displays flicker between rich and empty data.
    A snapshot without measurements counts as a failed read instead.
    """
    if snapshot is not None and snapshot.measurements is None:
        raise RuntimeError("partial CAN read: no battery measurements frame")
    return snapshot


def charge_limit_status_conditions(
    classic_settings: ClassicChargeSettings | None,
    battery: PylonCanSnapshot | None,
) -> list[str]:
    return [candidate.text for candidate in charge_limit_status_condition_candidates(classic_settings, battery)]


def charge_limit_status_condition_candidates(
    classic_settings: ClassicChargeSettings | None,
    battery: PylonCanSnapshot | None,
) -> list[StatusConditionCandidate]:
    if classic_settings is None or battery is None or battery.charge_limits is None:
        return []

    conditions: list[StatusConditionCandidate] = []
    limits = battery.charge_limits
    if classic_settings.battery_current_limit_a > limits.charge_current_limit_a:
        conditions.append(
            StatusConditionCandidate(
                "classic.0.ccl_exceeds_bms",
                "Charge controller 0 CCL exceeds battery CCL: "
                f"{classic_settings.battery_current_limit_a:.1f}A > {limits.charge_current_limit_a:.1f}A",
            )
        )

    voltage_setpoints = [
        ("Absorb", classic_settings.absorb_voltage_v),
        ("Float", classic_settings.float_voltage_v),
        ("Equalize", classic_settings.equalize_voltage_v),
        ("Max temp-comp", classic_settings.max_temp_comp_voltage_v),
    ]
    exceeded = [
        f"{label} {value:.1f}V"
        for label, value in voltage_setpoints
        if value > limits.charge_voltage_limit_v
    ]
    if exceeded:
        conditions.append(
            StatusConditionCandidate(
                "classic.0.cvs_exceeds_bms",
                "Charge controller 0 CVS exceeds battery CVL: "
                f"{', '.join(exceeded)} > {limits.charge_voltage_limit_v:.1f}V",
                severity=STATUS_ERROR,
            )
        )
    return conditions


def status_condition_severity(candidates: list[StatusConditionCandidate]) -> str:
    if any(candidate.severity == STATUS_ERROR for candidate in candidates):
        return STATUS_ERROR
    if candidates:
        return STATUS_WARNING
    return STATUS_OK


def cell_status_condition_candidates(battery: PylonCanSnapshot | None) -> list[StatusConditionCandidate]:
    if battery is None or battery.extended_measurements is None:
        return []

    extended = battery.extended_measurements
    candidates: list[StatusConditionCandidate] = []
    max_cell_v = extended.max_cell_voltage_v
    min_cell_v = extended.min_cell_voltage_v

    if max_cell_v is not None:
        if max_cell_v >= CELL_OVERVOLTAGE_ALERT_V:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.overvoltage",
                    f"Battery cell overvoltage risk: max cell {max_cell_v:.3f}V >= {CELL_OVERVOLTAGE_ALERT_V:.3f}V",
                    severity=STATUS_ERROR,
                )
            )
        elif max_cell_v >= CELL_HIGH_VOLTAGE_WARNING_V:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.high",
                    f"Battery cell high: max cell {max_cell_v:.3f}V >= {CELL_HIGH_VOLTAGE_WARNING_V:.3f}V",
                    required_samples=2,
                )
            )

    if min_cell_v is not None and max_cell_v is not None and max_cell_v >= CELL_DELTA_TOP_OF_CHARGE_V:
        delta_mv = round((max_cell_v - min_cell_v) * 1000)
        if delta_mv >= CELL_DELTA_CRITICAL_MV:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.delta.critical",
                    "Battery cell delta critical: "
                    f"{delta_mv}mV >= {CELL_DELTA_CRITICAL_MV}mV while max cell {max_cell_v:.3f}V >= {CELL_DELTA_TOP_OF_CHARGE_V:.3f}V",
                    required_samples=2,
                )
            )
        elif delta_mv >= CELL_DELTA_WARNING_MV:
            candidates.append(
                StatusConditionCandidate(
                    "battery.cell.delta.high",
                    "Battery cell delta high: "
                    f"{delta_mv}mV >= {CELL_DELTA_WARNING_MV}mV while max cell {max_cell_v:.3f}V >= {CELL_DELTA_TOP_OF_CHARGE_V:.3f}V",
                    required_samples=2,
                )
            )
    return candidates

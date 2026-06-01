"""Primitive HTML rendering and serving for supervisor metrics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.parse import urlparse

from .supervisor import Supervisor, SupervisorSnapshot
from .terminal_display import format_time


KINDLE_REFRESH_SECONDS = 60
MIDNIGHT_SOC_UNAVAILABLE = "00:00:00h SOC unavailable"
BATTERY_IDLE_CURRENT_A = 0.5


@dataclass(frozen=True)
class DisplayResponse:
    status: HTTPStatus
    content_type: str
    body: bytes


@dataclass(frozen=True)
class HouseholdLoadSummary:
    current_a: float
    power_w: int
    average_today_text: str | None = None
    today_text: str | None = None
    remaining_text: str | None = None


class SnapshotCache:
    def __init__(self) -> None:
        self._snapshot: SupervisorSnapshot | None = None
        self._household_load: HouseholdLoadSummary | None = None
        self._lock = Lock()

    def set(self, snapshot: SupervisorSnapshot, household_load: HouseholdLoadSummary | None = None) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._household_load = household_load

    def get(self) -> SupervisorSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("no supervisor snapshot has been captured yet")
            return self._snapshot

    def get_household_load(self) -> HouseholdLoadSummary | None:
        with self._lock:
            return self._household_load


def is_kindle_user_agent(user_agent: str) -> bool:
    normalized = user_agent.lower()
    return "kindle" in normalized or "silk/" in normalized


def render_kindle_snapshot(
    snapshot: SupervisorSnapshot,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    household_load: HouseholdLoadSummary | None = None,
) -> str:
    status = "OK" if snapshot.ok else "ERROR"
    updated = format_time(snapshot.captured_at)
    status_class = ' class="bad"' if not snapshot.ok else ""
    status_text = _status_text(snapshot, status)
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        f'<meta http-equiv="refresh" content="{refresh_seconds}">',
        "<title>Off-Grid Power</title>",
        "<style>",
        "body{font-family:serif;color:#000;background:#fff;margin:6px;font-size:18px;}",
        "h2{font-size:20px;margin:10px 0 3px 0;border-bottom:1px solid #000;}",
        "table{border-collapse:collapse;width:100%;}",
        "td{padding:1px 0;vertical-align:top;border-bottom:1px solid #ccc;}",
        "td:first-child{font-weight:bold;width:35%;}",
        ".bad{font-weight:bold;}",
        ".summary{font-size:20px;font-weight:bold;margin:0 0 6px 0;border-bottom:1px solid #000;padding-bottom:3px;}",
        ".updated{float:right;}",
        ".small{font-size:14px;}",
        "</style>",
        "</head>",
        "<body>",
        f'<p class="summary"><span{status_class}>{escape(status_text)}</span>'
        f'<span class="updated">Updated: {escape(updated)}</span></p>',
    ]
    lines.extend(_household_section(household_load))
    lines.extend(_battery_section(snapshot))
    lines.extend(_charge_controller_sections(snapshot))
    lines.extend(_temperature_section(snapshot))

    if snapshot.errors:
        lines.append("<h2>Errors</h2>")
        lines.append("<ul>")
        for error in snapshot.errors:
            lines.append(f"<li>{escape(error)}</li>")
        lines.append("</ul>")

    lines.extend(
        [
            f'<p class="small">Refreshes every {refresh_seconds} seconds. Read-only monitor.</p>',
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)


def route_display_request(
    snapshot: SupervisorSnapshot,
    path: str,
    user_agent: str,
    household_load: HouseholdLoadSummary | None = None,
) -> DisplayResponse:
    parsed_path = urlparse(path).path
    if parsed_path not in {"/", "/kindle", "/display", "/healthz"}:
        return DisplayResponse(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
    if parsed_path == "/healthz":
        status = HTTPStatus.OK if snapshot.ok else HTTPStatus.SERVICE_UNAVAILABLE
        body = b"ok\n" if snapshot.ok else b"error\n"
        return DisplayResponse(status, "text/plain; charset=utf-8", body)

    html = render_kindle_snapshot(snapshot, household_load=household_load)
    content_type = "text/html; charset=utf-8"
    if parsed_path == "/kindle" or is_kindle_user_agent(user_agent):
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))


def run_display_server(
    supervisor: Supervisor,
    host: str = "0.0.0.0",
    port: int = 8080,
    snapshot_provider: Callable[[], SupervisorSnapshot] | None = None,
    household_load_provider: Callable[[], HouseholdLoadSummary | None] | None = None,
    access_log_path: str | None = None,
) -> None:
    provider = snapshot_provider or supervisor.read_snapshot
    logger = AccessLogger(access_log_path)
    load_tracker = HouseholdLoadTracker()

    class Handler(BaseHTTPRequestHandler):
        server_version = "OffGridPowerDisplay/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            try:
                snapshot = provider()
            except Exception as exc:  # noqa: BLE001 - HTTP display should show readiness errors.
                body = f"snapshot unavailable: {exc}\n".encode("utf-8")
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE.value)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                logger.log(
                    self.client_address[0],
                    self.path,
                    self.headers.get("User-Agent", ""),
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            if urlparse(self.path).path == "/healthz":
                household_load = None
            elif household_load_provider is not None:
                household_load = household_load_provider()
            else:
                household_load = load_tracker.update(snapshot)
            response = route_display_request(
                snapshot,
                self.path,
                self.headers.get("User-Agent", ""),
                household_load=household_load,
            )
            status = response.status
            self.send_response(response.status.value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)
            logger.log(self.client_address[0], self.path, self.headers.get("User-Agent", ""), status)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
            return

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


class AccessLogger:
    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None

    def log(self, client: str, path: str, user_agent: str, status: HTTPStatus) -> None:
        line = (
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
            f"{client} {status.value} {path!r} {user_agent!r}\n"
        )
        if self.path is None:
            print(line, end="", flush=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)


class HouseholdLoadTracker:
    def __init__(self, midnight_soc_log_path: str | None = "data/household-soc-baselines.csv") -> None:
        self.midnight_soc_log_path = Path(midnight_soc_log_path) if midnight_soc_log_path else None
        self._midnight_soc_by_day: dict[str, int] | None = None

    def update(self, snapshot: SupervisorSnapshot) -> HouseholdLoadSummary | None:
        current_a = estimate_household_load_current_a(snapshot)
        if current_a is None:
            return None

        voltage_v = household_load_voltage_v(snapshot)
        capacity_ah = bank_capacity_ah(snapshot)
        midnight_soc = self._midnight_soc_for_snapshot(snapshot)
        return HouseholdLoadSummary(
            current_a=current_a,
            power_w=round(current_a * voltage_v),
            average_today_text=estimate_household_average_today_text(snapshot, capacity_ah, midnight_soc),
            today_text=estimate_household_today_text(snapshot, capacity_ah, midnight_soc),
            remaining_text=estimate_household_remaining_text(snapshot, capacity_ah, midnight_soc),
        )

    def _midnight_soc_for_snapshot(self, snapshot: SupervisorSnapshot) -> int | None:
        if snapshot.battery is None or snapshot.battery.state_of_charge is None:
            return None

        captured_at = snapshot.captured_at.astimezone()
        day = captured_at.date().isoformat()
        midnight_soc = self._read_midnight_soc_by_day().get(day)
        if midnight_soc is not None:
            return midnight_soc

        if _seconds_since_midnight(captured_at) <= 300:
            midnight_soc = snapshot.battery.state_of_charge.soc_percent
            self._write_midnight_soc(day, captured_at, midnight_soc)
            return midnight_soc
        return None

    def _read_midnight_soc_by_day(self) -> dict[str, int]:
        if self._midnight_soc_by_day is not None:
            return self._midnight_soc_by_day

        self._midnight_soc_by_day = {}
        if self.midnight_soc_log_path is None or not self.midnight_soc_log_path.exists():
            return self._midnight_soc_by_day

        with self.midnight_soc_log_path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                try:
                    self._midnight_soc_by_day[row["day"]] = int(row["soc_percent"])
                except (KeyError, ValueError):
                    continue
        return self._midnight_soc_by_day

    def _write_midnight_soc(self, day: str, captured_at: datetime, soc_percent: int) -> None:
        if self.midnight_soc_log_path is None:
            return

        baselines = self._read_midnight_soc_by_day()
        if day in baselines:
            return

        self.midnight_soc_log_path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.midnight_soc_log_path.exists() or self.midnight_soc_log_path.stat().st_size == 0
        with self.midnight_soc_log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if needs_header:
                writer.writerow(["day", "captured_at", "soc_percent"])
            writer.writerow([day, captured_at.isoformat(), soc_percent])
        baselines[day] = soc_percent


def estimate_household_load_current_a(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.classic is None or snapshot.battery is None or snapshot.battery.measurements is None:
        return None
    # Classic current is charger output. BMS current is net battery current,
    # positive while charging and negative while discharging.
    return snapshot.classic.battery_current_a - snapshot.battery.measurements.current_a


def household_load_voltage_v(snapshot: SupervisorSnapshot) -> float:
    if snapshot.battery is not None and snapshot.battery.measurements is not None:
        return snapshot.battery.measurements.voltage_v
    if snapshot.classic is not None:
        return snapshot.classic.battery_voltage_v
    return 0.0


def household_today_text(today_ah: float, bank_percent: float | None) -> str:
    text = f"{today_ah:.1f}Ah"
    if bank_percent is not None:
        text += f" {bank_percent:.1f}% of bank"
    return text


def estimate_household_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str:
    today_ah = estimate_household_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return MIDNIGHT_SOC_UNAVAILABLE
    return household_today_text(today_ah, today_ah / bank_capacity * 100)


def estimate_household_today_ah(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> float | None:
    if (
        snapshot.classic is None
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None
    if midnight_soc_percent is None:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    battery_delta_ah = (current_soc_percent - midnight_soc_percent) / 100 * bank_capacity
    return snapshot.classic.daily_amp_hours_ah - battery_delta_ah


def estimate_household_average_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_household_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_a = today_ah / elapsed_hours
    average_w = round(average_a * household_load_voltage_v(snapshot))
    return f"{average_a:.1f}A  {average_w}W"


def estimate_household_remaining_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_household_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if (
        today_ah is None
        or today_ah <= 0
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_usage_a = today_ah / elapsed_hours
    if average_usage_a <= 0:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_usage_a
    return f"{remaining_hours:.1f}h"


def bank_capacity_ah(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.battery is None or snapshot.battery.extended_measurements is None:
        return None
    return snapshot.battery.extended_measurements.installed_capacity_ah


def _seconds_since_midnight(value: datetime) -> float:
    return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1_000_000


def _charge_controller_sections(snapshot: SupervisorSnapshot) -> list[str]:
    lines: list[str] = []
    controllers = [(0, snapshot.classic)]
    for index, classic in controllers:
        lines.extend([f"<h2>Charge Controller {index}</h2>", "<table>"])
        if classic is None:
            lines.append(_row("State", "No data"))
        else:
            lines.extend(
                [
                    _row(
                        "PV",
                        f"{classic.pv_voltage_v:.1f}V  {classic.pv_current_a:.1f}A",
                    ),
                    _row(
                        "Battery",
                        f"{classic.battery_voltage_v:.1f}V  {classic.battery_current_a:.1f}A  {classic.battery_power_w}W",
                    ),
                    _row("Stage", _stage_value(classic.charge_stage, classic.state)),
                    _row("Today Cumulative", f"{classic.daily_energy_kwh:.1f}kWh  {classic.daily_amp_hours_ah}Ah"),
                ]
            )
        lines.append("</table>")
    return lines


def _household_section(household_load: HouseholdLoadSummary | None) -> list[str]:
    lines = ["<h2>Load</h2>", "<table>"]
    if household_load is None:
        lines.append(_row("Now", "No data"))
    else:
        lines.append(_row("Now", f"{household_load.current_a:.1f}A  {household_load.power_w}W"))
        if household_load.average_today_text is not None:
            lines.append(_row("Average Today", household_load.average_today_text))
        if household_load.today_text is not None:
            lines.append(_row("Cumulative Today", household_load.today_text))
        if household_load.remaining_text is not None:
            lines.append(_row("Estimated Autonomy", household_load.remaining_text))
    lines.append("</table>")
    return lines


def _battery_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Battery Bank</h2>", "<table>"]
    battery = snapshot.battery
    if battery is None:
        lines.append(_row("State", "No CAN data"))
        lines.append("</table>")
        return lines

    if battery.measurements is not None:
        measurements = battery.measurements
        lines.append(_row("Pack", f"{measurements.voltage_v:.2f}V  {measurements.current_a:.1f}A  {_battery_state(measurements.current_a)}"))
    if battery.request_flags is not None:
        flags = battery.request_flags
        charge = "yes" if flags.charge_enable else "no"
        discharge = "yes" if flags.discharge_enable else "no"
        extra_requests = []
        if flags.force_charge_1 or flags.force_charge_2:
            extra_requests.append("force charge")
        if flags.full_charge_request:
            extra_requests.append("full charge")
        suffix = f"  Request: {', '.join(extra_requests)}" if extra_requests else ""
        lines.append(_row("Enable", f"charge {charge}  discharge {discharge}{suffix}"))
    if (
        battery.extended_measurements is not None
        and battery.extended_measurements.min_cell_voltage_v is not None
        and battery.extended_measurements.max_cell_voltage_v is not None
    ):
        extended = battery.extended_measurements
        delta_mv = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)
        value = f"{extended.min_cell_voltage_v:.3f}-{extended.max_cell_voltage_v:.3f}V ({delta_mv}mV delta)"
        if extended.min_cell_temperature_c is not None and extended.max_cell_temperature_c is not None:
            value += f"  {extended.min_cell_temperature_c:.1f}-{extended.max_cell_temperature_c:.1f}C"
        lines.append(_row("Cells", value))
    if battery.status is not None:
        status = battery.status
        active_conditions = [*status.protection_flags, *status.alarm_flags]
        if active_conditions:
            lines.append(_row("Protection/Alarms", ", ".join(active_conditions)))
        else:
            lines.append(_row("Protection/Alarms", "none"))
    lines.append("</table>")
    return lines


def _temperature_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Temperatures</h2>", "<table>"]
    if snapshot.battery is not None and snapshot.battery.measurements is not None:
        lines.append(_row("Battery", f"{snapshot.battery.measurements.temperature_c:.1f} C"))
    if snapshot.classic is not None:
        classic = snapshot.classic
        lines.append(_row("Controller 0 FET", f"{classic.fet_temp_c:.1f} C"))
        lines.append(_row("Controller 0 PCB", f"{classic.pcb_temp_c:.1f} C"))
    if snapshot.ambient is None:
        lines.append(_row("Ambient", "disconnected"))
    else:
        lines.append(_row("Ambient", f"{snapshot.ambient.temperature_c:.1f} C"))
        if snapshot.ambient.humidity_percent is not None:
            lines.append(_row("Humidity", f"{snapshot.ambient.humidity_percent:.1f}%"))
    lines.append("</table>")
    return lines


def _row(label: str, value: str, css_class: str = "") -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return f"<tr><td>{escape(label)}</td><td{class_attr}>{escape(value)}</td></tr>"


def _battery_state(current_a: float) -> str:
    if current_a > BATTERY_IDLE_CURRENT_A:
        return "charging"
    if current_a < -BATTERY_IDLE_CURRENT_A:
        return "discharging"
    return "idle"


def _stage_value(charge_stage: str, state: str) -> str:
    if state == charge_stage:
        return charge_stage
    return f"{charge_stage}  State: {state}"


def _status_text(snapshot: SupervisorSnapshot, status: str) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return f"Status: {status}"
    return f"SOC: {snapshot.battery.state_of_charge.soc_percent}%  Status: {status}"


def _row_lines(label: str, values: list[str]) -> str:
    escaped_values = "<br>".join(escape(value) for value in values)
    return f"<tr><td>{escape(label)}</td><td>{escaped_values}</td></tr>"

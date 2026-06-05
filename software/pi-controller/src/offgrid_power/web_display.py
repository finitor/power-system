"""Primitive HTML rendering and serving for supervisor metrics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
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
ROLLING_LOAD_WINDOW = timedelta(hours=3)


@dataclass(frozen=True)
class DisplayResponse:
    status: HTTPStatus
    content_type: str
    body: bytes


@dataclass(frozen=True)
class LoadSummary:
    current_a: float
    power_w: int
    average_today_text: str | None = None
    today_text: str | None = None
    remaining_text: str | None = None
    rolling_average_a: float | None = None
    rolling_average_w: float | None = None


@dataclass(frozen=True)
class LoadSample:
    captured_at: datetime
    current_a: float
    power_w: int
    soc_percent: int | None = None
    voltage_v: float | None = None


class SnapshotCache:
    def __init__(self) -> None:
        self._snapshot: SupervisorSnapshot | None = None
        self._load_summary: LoadSummary | None = None
        self._lock = Lock()

    def set(self, snapshot: SupervisorSnapshot, load_summary: LoadSummary | None = None) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._load_summary = load_summary

    def get(self) -> SupervisorSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("no supervisor snapshot has been captured yet")
            return self._snapshot

    def get_load_summary(self) -> LoadSummary | None:
        with self._lock:
            return self._load_summary


def is_kindle_user_agent(user_agent: str) -> bool:
    normalized = user_agent.lower()
    return "kindle" in normalized or "silk/" in normalized


def render_kindle_snapshot(
    snapshot: SupervisorSnapshot,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    load_summary: LoadSummary | None = None,
) -> str:
    status = snapshot.status_text
    updated = format_time(snapshot.captured_at)
    status_class = ' class="bad"' if status == "ERROR" else ""
    status_class_attr = f' class="meta-cell {status_class[8:-1]}"' if status_class else ' class="meta-cell"'
    soc_text = _soc_text(snapshot)
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        f'<meta http-equiv="refresh" content="{refresh_seconds}">',
        "<title>Off-Grid Power</title>",
        "<style>",
        "body{font-family:serif;color:#000;background:#fff;margin:4px;font-size:17px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
        "h2{font-size:19px;margin:8px 0 2px 0;border-bottom:1px solid #000;}",
        "table{border-collapse:collapse;width:100%;}",
        "td{font-size:17px;line-height:1.18;padding:1px 0;vertical-align:top;border-bottom:1px solid #ccc;}",
        "td:first-child{font-size:17px;font-weight:bold;width:32%;}",
        ".bad{font-weight:bold;}",
        ".summary-table{margin:0 0 6px 0;border-bottom:1px solid #000;}",
        ".summary-table td{font-size:19px;font-weight:bold;border-bottom:0;padding-bottom:2px;}",
        ".summary-table .soc-cell{font-size:36px;line-height:1;text-align:left;vertical-align:middle;width:32%;}",
        ".summary-table .meta-cell{font-size:17px;line-height:1.15;text-align:left;width:68%;}",
        ".small{font-size:13px;}",
        "</style>",
        "</head>",
        "<body>",
        '<table class="summary-table">',
        f'<tr><td class="soc-cell" rowspan="2">SOC {escape(soc_text)}</td><td class="meta-cell">Updated: {escape(updated)}</td></tr>',
        f"<tr><td{status_class_attr}>Status: {escape(status)}</td></tr>",
        "</table>",
    ]
    lines.extend(_load_section(load_summary))
    lines.extend(_battery_section(snapshot))
    lines.extend(_charge_controller_sections(snapshot))
    lines.extend(_temperature_section(snapshot))

    if snapshot.errors:
        lines.append("<h2>Errors</h2>")
        lines.append("<ul>")
        for error in snapshot.errors:
            lines.append(f"<li>{escape(error)}</li>")
        lines.append("</ul>")
    if snapshot.status_conditions:
        lines.append("<h2>Status Conditions</h2>")
        lines.append("<ul>")
        for condition in snapshot.status_conditions:
            lines.append(f"<li>{escape(condition)}</li>")
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
    load_summary: LoadSummary | None = None,
) -> DisplayResponse:
    parsed_path = urlparse(path).path
    if parsed_path not in {"/", "/kindle", "/display", "/healthz"}:
        return DisplayResponse(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
    if parsed_path == "/healthz":
        status = HTTPStatus.OK if snapshot.ok else HTTPStatus.SERVICE_UNAVAILABLE
        body = b"ok\n" if snapshot.ok else b"error\n"
        return DisplayResponse(status, "text/plain; charset=utf-8", body)

    html = render_kindle_snapshot(snapshot, load_summary=load_summary)
    content_type = "text/html; charset=utf-8"
    if parsed_path == "/kindle" or is_kindle_user_agent(user_agent):
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))


def run_display_server(
    supervisor: Supervisor,
    host: str = "0.0.0.0",
    port: int = 8080,
    snapshot_provider: Callable[[], SupervisorSnapshot] | None = None,
    load_summary_provider: Callable[[], LoadSummary | None] | None = None,
    access_log_path: str | None = None,
) -> None:
    provider = snapshot_provider or supervisor.read_snapshot
    logger = AccessLogger(access_log_path)
    load_tracker = LoadTracker(sample_buffer=LoadSampleBuffer())

    class Handler(BaseHTTPRequestHandler):
        server_version = "OffGridPowerDisplay/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            try:
                snapshot = provider()
            except Exception as exc:  # noqa: BLE001 - HTTP display should show readiness errors.
                body = render_snapshot_unavailable(exc).encode("utf-8")
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE.value)
                self.send_header("Content-Type", "text/html; charset=utf-8")
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
                load_summary = None
            elif load_summary_provider is not None:
                load_summary = load_summary_provider()
            else:
                load_summary = load_tracker.update(snapshot)
            response = route_display_request(
                snapshot,
                self.path,
                self.headers.get("User-Agent", ""),
                load_summary=load_summary,
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


class LoadTracker:
    def __init__(
        self,
        midnight_soc_log_path: str | None = "data/load-soc-baselines.csv",
        sample_buffer: "LoadSampleBuffer | None" = None,
    ) -> None:
        self.midnight_soc_log_path = Path(midnight_soc_log_path) if midnight_soc_log_path else None
        self._midnight_soc_by_day: dict[str, int] | None = None
        self.sample_buffer = sample_buffer

    def update(self, snapshot: SupervisorSnapshot) -> LoadSummary | None:
        current_a = estimate_load_current_a(snapshot)
        if current_a is None:
            return None

        voltage_v = load_voltage_v(snapshot)
        capacity_ah = bank_capacity_ah(snapshot)
        midnight_soc = self._midnight_soc_for_snapshot(snapshot)
        current_summary = LoadSummary(
            current_a=current_a,
            power_w=round(current_a * voltage_v),
        )
        rolling_average = None
        if self.sample_buffer is not None:
            self.sample_buffer.append(snapshot, current_summary)
            rolling_average = self.sample_buffer.rolling_average(
                now=snapshot.captured_at,
                window=ROLLING_LOAD_WINDOW,
            )
        summary = LoadSummary(
            current_a=current_a,
            power_w=current_summary.power_w,
            average_today_text=rolling_load_average_text(rolling_average),
            today_text=estimate_load_today_text(snapshot, capacity_ah, midnight_soc),
            remaining_text=estimate_load_remaining_from_average_a(
                snapshot,
                capacity_ah,
                None if rolling_average is None else rolling_average[0],
            ),
            rolling_average_a=None if rolling_average is None else rolling_average[0],
            rolling_average_w=None if rolling_average is None else rolling_average[1],
        )
        return summary

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


class LoadSampleBuffer:
    FIELDNAMES = ["captured_at", "current_a", "power_w", "soc_percent", "voltage_v"]

    def __init__(
        self,
        path: str | None = "data/load-samples.csv",
        retention: timedelta = timedelta(hours=24),
        prune_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        self.path = Path(path) if path else None
        self.retention = retention
        self.prune_interval = prune_interval
        self._last_prune_at: datetime | None = None
        self._lock = Lock()

    def append(self, snapshot: SupervisorSnapshot, summary: LoadSummary) -> None:
        if self.path is None:
            return

        sample = LoadSample(
            captured_at=snapshot.captured_at.astimezone(),
            current_a=summary.current_a,
            power_w=summary.power_w,
            soc_percent=_snapshot_soc_percent(snapshot),
            voltage_v=load_voltage_v(snapshot),
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            needs_header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
                if needs_header:
                    writer.writeheader()
                writer.writerow(self._sample_row(sample))
            if self._should_prune(sample.captured_at):
                self._prune_locked(sample.captured_at)

    def samples(self, now: datetime | None = None, window: timedelta | None = None) -> list[LoadSample]:
        if self.path is None or not self.path.exists():
            return []

        reference = (now or datetime.now().astimezone()).astimezone()
        cutoff = reference - (window if window is not None else self.retention)
        with self._lock:
            return [sample for sample in self._read_samples_locked() if sample.captured_at >= cutoff]

    def rolling_average(self, now: datetime | None = None, window: timedelta = timedelta(hours=1)) -> tuple[float, float] | None:
        samples = self.samples(now=now, window=window)
        if not samples:
            return None
        average_a = sum(sample.current_a for sample in samples) / len(samples)
        average_w = sum(sample.power_w for sample in samples) / len(samples)
        return average_a, average_w

    def prune(self, now: datetime | None = None) -> None:
        if self.path is None:
            return
        reference = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            self._prune_locked(reference)

    def _should_prune(self, captured_at: datetime) -> bool:
        if self._last_prune_at is None:
            return True
        return captured_at - self._last_prune_at >= self.prune_interval

    def _prune_locked(self, now: datetime) -> None:
        if self.path is None or not self.path.exists():
            return

        cutoff = now - self.retention
        samples = [sample for sample in self._read_samples_locked() if sample.captured_at >= cutoff]
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            writer.writeheader()
            for sample in samples:
                writer.writerow(self._sample_row(sample))
        self._last_prune_at = now

    def _read_samples_locked(self) -> list[LoadSample]:
        if self.path is None or not self.path.exists():
            return []

        samples: list[LoadSample] = []
        with self.path.open("r", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                sample = self._sample_from_row(row)
                if sample is not None:
                    samples.append(sample)
        return samples

    def _sample_row(self, sample: LoadSample) -> dict[str, str]:
        return {
            "captured_at": sample.captured_at.isoformat(),
            "current_a": f"{sample.current_a:.3f}",
            "power_w": str(sample.power_w),
            "soc_percent": "" if sample.soc_percent is None else str(sample.soc_percent),
            "voltage_v": "" if sample.voltage_v is None else f"{sample.voltage_v:.3f}",
        }

    def _sample_from_row(self, row: dict[str, str]) -> LoadSample | None:
        try:
            captured_at = datetime.fromisoformat(row["captured_at"]).astimezone()
            soc_text = row.get("soc_percent", "")
            voltage_text = row.get("voltage_v", "")
            return LoadSample(
                captured_at=captured_at,
                current_a=float(row["current_a"]),
                power_w=int(row["power_w"]),
                soc_percent=None if not soc_text else int(soc_text),
                voltage_v=None if not voltage_text else float(voltage_text),
            )
        except (KeyError, TypeError, ValueError):
            return None


def estimate_load_current_a(snapshot: SupervisorSnapshot) -> float | None:
    if snapshot.classic is None or snapshot.battery is None or snapshot.battery.measurements is None:
        return None
    # Classic current is charger output. BMS current is net battery current,
    # positive while charging and negative while discharging.
    return snapshot.classic.battery_current_a - snapshot.battery.measurements.current_a


def load_voltage_v(snapshot: SupervisorSnapshot) -> float:
    if snapshot.battery is not None and snapshot.battery.measurements is not None:
        return snapshot.battery.measurements.voltage_v
    if snapshot.classic is not None:
        return snapshot.classic.battery_voltage_v
    return 0.0


def _snapshot_soc_percent(snapshot: SupervisorSnapshot) -> int | None:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return None
    return snapshot.battery.state_of_charge.soc_percent


def load_today_text(today_ah: float, bank_percent: float | None) -> str:
    text = f"{today_ah:.1f}Ah"
    if bank_percent is not None:
        text += f" {bank_percent:.1f}% of bank"
    return text


def estimate_load_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return MIDNIGHT_SOC_UNAVAILABLE
    return load_today_text(today_ah, today_ah / bank_capacity * 100)


def estimate_load_today_ah(
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


def estimate_load_average_today_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
    if today_ah is None:
        return None

    elapsed_hours = _seconds_since_midnight(snapshot.captured_at.astimezone()) / 3600
    if elapsed_hours <= 0:
        return None

    average_a = today_ah / elapsed_hours
    average_w = round(average_a * load_voltage_v(snapshot))
    return f"{average_a:.1f}A  {average_w}W"


def estimate_load_remaining_text(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    midnight_soc_percent: int | None,
) -> str | None:
    today_ah = estimate_load_today_ah(snapshot, bank_capacity, midnight_soc_percent)
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

    average_load_a = today_ah / elapsed_hours
    if average_load_a <= 0:
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_load_a
    return f"{remaining_hours:.1f}h"


def rolling_load_average_text(rolling_average: tuple[float, float] | None) -> str | None:
    if rolling_average is None:
        return None
    average_a, average_w = rolling_average
    return f"{average_a:.1f}A  {round(average_w)}W"


def estimate_load_remaining_from_average_a(
    snapshot: SupervisorSnapshot,
    bank_capacity: float | None,
    average_load_a: float | None,
) -> str | None:
    if (
        average_load_a is None
        or average_load_a <= 0
        or snapshot.battery is None
        or snapshot.battery.state_of_charge is None
        or bank_capacity is None
        or bank_capacity <= 0
    ):
        return None

    current_soc_percent = snapshot.battery.state_of_charge.soc_percent
    remaining_ah = current_soc_percent / 100 * bank_capacity
    remaining_hours = remaining_ah / average_load_a
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
                        f"{classic.pv_voltage_v:.1f}V  {classic.pv_current_a:.1f}A  Voc {classic.last_voc_v:.1f}V",
                    ),
                    _row(
                        "Output",
                        f"{classic.battery_voltage_v:.1f}V  {classic.battery_current_a:.1f}A  {classic.battery_power_w}W",
                    ),
                    _row("Charge Status", _stage_value(classic.charge_stage, classic.state)),
                    *(
                        [
                            _row(
                                "PV input",
                                f"HyperVOC protection  Last Voc {classic.last_voc_v:.1f}V  High {classic.highest_input_voltage_v:.1f}V",
                            )
                        ]
                        if classic.is_hypervoc
                        else []
                    ),
                    _row("Production Today", f"{classic.daily_energy_kwh:.1f}kWh  {classic.daily_amp_hours_ah}Ah"),
                    _row("Temps", f"batt {classic.battery_temp_c:.1f}C  FET {classic.fet_temp_c:.1f}C  PCB {classic.pcb_temp_c:.1f}C"),
                ]
            )
            if index == 0 and snapshot.classic_settings is not None:
                settings = snapshot.classic_settings
                lines.append(
                    _row(
                        "Charge Settings",
                        f"Limit {settings.battery_current_limit_a:.1f}A  "
                        f"Absorb {settings.absorb_voltage_v:.1f}V for {settings.absorb_time_s}s  "
                        f"Float {settings.float_voltage_v:.1f}V  "
                        f"EQ {settings.equalize_voltage_v:.1f}V",
                    )
                )
        lines.append("</table>")
    return lines


def _load_section(load_summary: LoadSummary | None) -> list[str]:
    lines = ["<h2>Load</h2>", "<table>"]
    if load_summary is None:
        lines.append(_row("Now", "No data"))
    else:
        lines.append(_row("Now", f"{load_summary.current_a:.1f}A  {load_summary.power_w}W"))
        if load_summary.average_today_text is not None:
            lines.append(_row("3hr Rolling Avg", load_summary.average_today_text))
        if load_summary.today_text is not None:
            lines.append(_row("Cumulative Today", load_summary.today_text))
        if load_summary.remaining_text is not None:
            lines.append(_row("Estimated Autonomy", load_summary.remaining_text))
    lines.append("</table>")
    return lines


def _battery_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Battery Bank</h2>", "<table>"]
    battery = snapshot.battery
    if battery is None:
        for label, value in _missing_battery_rows(snapshot):
            lines.append(_row(label, value))
        lines.append("</table>")
        return lines

    if battery.measurements is not None:
        measurements = battery.measurements
        power_w = round(measurements.voltage_v * measurements.current_a)
        lines.append(_row("Flow", f"{measurements.voltage_v:.2f}V  {measurements.current_a:.1f}A  {power_w}W  {_battery_state(measurements.current_a)}"))
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
    if battery.charge_limits is not None:
        limits = battery.charge_limits
        lines.append(
            _row(
                "Limits",
                f"charge {limits.charge_voltage_limit_v:.1f}V/{limits.charge_current_limit_a:.1f}A  "
                f"discharge {limits.discharge_current_limit_a:.1f}A",
            )
        )
    lines.append("</table>")
    return lines


def _temperature_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Temperatures</h2>", "<table>"]
    if (
        snapshot.battery is not None
        and snapshot.battery.extended_measurements is not None
        and snapshot.battery.extended_measurements.min_cell_temperature_c is not None
        and snapshot.battery.extended_measurements.max_cell_temperature_c is not None
    ):
        extended = snapshot.battery.extended_measurements
        lines.append(_row("Battery cells", f"{extended.min_cell_temperature_c:.1f}-{extended.max_cell_temperature_c:.1f}C"))
    if snapshot.classic is not None:
        classic = snapshot.classic
        lines.append(_row("Battery terminal", f"{classic.battery_temp_c:.1f}C"))
        lines.append(_row("Charge controller FET", f"{classic.fet_temp_c:.1f}C"))
        lines.append(_row("Charge controller PCB", f"{classic.pcb_temp_c:.1f}C"))
    if snapshot.ambient is None:
        lines.append(_row("Sensor 0 ambient temp", "disconnected"))
    else:
        lines.append(_row("Sensor 0 ambient temp", f"{snapshot.ambient.temperature_c:.1f}C"))
        if snapshot.ambient.humidity_percent is not None:
            lines.append(_row("Humidity", f"{snapshot.ambient.humidity_percent:.1f}%"))
    lines.append("</table>")
    return lines


def _missing_battery_rows(snapshot: SupervisorSnapshot) -> list[tuple[str, str]]:
    if snapshot.battery_can_health is None:
        return [("State", "No CAN data")]
    if snapshot.battery_can_health.dfu_devices:
        devices = []
        for device in snapshot.battery_can_health.dfu_devices[:2]:
            product = device.product or "STM32 DFU"
            serial = f" serial {device.serial}" if device.serial else ""
            devices.append(f"{product}{serial}")
        return [
            ("CAN adapter", "DFU/bootloader mode"),
            ("DFU devices", "; ".join(devices)),
            ("Action", "replug USB-CAN adapter without BOOT/DFU pressed"),
        ]
    if not snapshot.battery_can_health.socketcan_present:
        return [("CAN adapter", f"interface {snapshot.battery_can_health.interface} not present")]
    return [("State", "No CAN frames received")]


def render_snapshot_unavailable(exc: Exception, refresh_seconds: int = 10) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            "<html>",
            "<head>",
            '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
            f'<meta http-equiv="refresh" content="{refresh_seconds}">',
            "<title>Off-Grid Power</title>",
            "<style>",
            "body{font-family:serif;color:#000;background:#fff;margin:8px;font-size:16px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
            ".small{font-size:12px;}",
            "</style>",
            "</head>",
            "<body>",
            "<h2>Snapshot unavailable</h2>",
            f"<p>{escape(str(exc))}</p>",
            f'<p class="small">Retrying every {refresh_seconds} seconds.</p>',
            "</body>",
            "</html>",
        ]
    )


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
    stage_value = f"Stage: {charge_stage}"
    if state == charge_stage:
        return stage_value
    return f"{stage_value}  State: {state}"


def _status_text(snapshot: SupervisorSnapshot, status: str) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return f"Status: {status}"
    return f"SOC: {snapshot.battery.state_of_charge.soc_percent}%  Status: {status}"


def _soc_text(snapshot: SupervisorSnapshot) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return "SOC --"
    return f"{snapshot.battery.state_of_charge.soc_percent}%"


def _row_lines(label: str, values: list[str]) -> str:
    escaped_values = "<br>".join(escape(value) for value in values)
    return f"<tr><td>{escape(label)}</td><td>{escaped_values}</td></tr>"

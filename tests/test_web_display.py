from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from offgrid_power.canbus import CanFrame, PylonCanSnapshot, PylonStatus, decode_pylon_snapshot
from offgrid_power.charge_ceiling import ChargeCeiling
from offgrid_power.classic import ClassicChargeSettings
from offgrid_power.load import LoadSummary
from offgrid_power.supervisor import STATUS_ERROR, Supervisor
from offgrid_power.web_display import (
    SnapshotCache,
    is_kindle_user_agent,
    render_browser_weather,
    render_kindle_details,
    render_kindle_snapshot,
    render_kindle_weather,
    _protection_text,
    render_snapshot_unavailable,
    route_control_request,
    route_display_request,
    snapshot_api_payload,
    wants_source_refresh,
    wants_weather_refresh,
)
from offgrid_power.weather import WeatherReport, weather_api_payload
from snapshot_helpers import make_battery_snapshot, make_classic_telemetry, make_epever_settings, make_epever_telemetry, make_magnum_snapshot, make_snapshot
from golden import check_golden


class FakeControlSupervisor:
    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot or make_snapshot(
            classic_settings=make_classic_settings(),
            epever_settings=make_epever_settings(charging_limit_voltage_v=60.0),
            battery=make_battery_with_cvl(58.4),
        )
        self.voltage_calls = []
        self.current_calls = []
        self.classic_calls = []
        self.time_calls = []
        self.charge_calls = []
        self.last_boost_reconnect_v = 53.6
        self.last_boost_v = 54.7
        self.last_float_v = 53.6
        self.last_equalize_v = 54.7
        self.last_current_a = None

    def read_snapshot(self):
        return self.snapshot

    def read_classic_settings(self):
        return self.snapshot.classic_settings or make_classic_settings()

    def read_epever_settings(self):
        return self.snapshot.epever_settings or make_epever_settings()

    def write_classic_charge_settings(self, **kwargs):
        self.classic_calls.append(kwargs)
        current = self.snapshot.classic_settings or make_classic_settings()
        return make_classic_settings(
            battery_current_limit_a=kwargs.get("battery_current_limit_a", current.battery_current_limit_a),
            absorb_voltage_v=kwargs.get("absorb_voltage_v", current.absorb_voltage_v),
            float_voltage_v=kwargs.get("float_voltage_v", current.float_voltage_v),
            equalize_voltage_v=kwargs.get("equalize_voltage_v", current.equalize_voltage_v),
            absorb_time_s=kwargs.get("absorb_time_s", current.absorb_time_s),
            max_temp_comp_voltage_v=kwargs.get("max_temp_comp_voltage_v", current.max_temp_comp_voltage_v),
        )

    def write_epever_charge_voltages(self, **kwargs):
        self.voltage_calls.append(kwargs)
        self.last_boost_reconnect_v = kwargs.get("boost_reconnect_v", self.last_boost_reconnect_v)
        self.last_boost_v = kwargs.get("boost_v", self.last_boost_v)
        self.last_float_v = kwargs.get("float_v", self.last_float_v)
        self.last_equalize_v = kwargs.get("equalize_v", self.last_equalize_v)
        return make_epever_settings(
            boost_voltage_v=self.last_boost_v,
            float_voltage_v=self.last_float_v,
            equalize_voltage_v=self.last_equalize_v,
            boost_reconnect_voltage_v=self.last_boost_reconnect_v,
        )

    def write_epever_max_charging_current(self, current_a):
        self.current_calls.append(current_a)
        self.last_current_a = current_a
        return make_epever_settings(
            boost_voltage_v=self.last_boost_v,
            boost_reconnect_voltage_v=self.last_boost_reconnect_v,
            max_charging_current_a=current_a,
        )

    def write_epever_charge_times(self, **kwargs):
        self.time_calls.append(kwargs)
        return make_epever_settings(
            boost_voltage_v=self.last_boost_v,
            boost_reconnect_voltage_v=self.last_boost_reconnect_v,
            max_charging_current_a=self.last_current_a,
            boost_time_minutes=kwargs.get("boost_time_minutes", 120),
            equalize_time_minutes=kwargs.get("equalize_time_minutes", 10),
        )

    def set_epever_charging(self, enabled):
        self.charge_calls.append(enabled)
        return enabled

    def write_magnum_charge_settings(self, **kwargs):
        raise NotImplementedError("Magnum charge-setting writes are not implemented")


def make_classic_settings(**overrides) -> ClassicChargeSettings:
    fields = {
        "captured_at": datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        "battery_current_limit_a": 80.0,
        "absorb_voltage_v": 55.2,
        "float_voltage_v": 54.0,
        "equalize_voltage_v": 55.2,
        "sliding_current_limit_a": 800,
        "absorb_time_s": 300,
        "max_temp_comp_voltage_v": 55.2,
        "min_temp_comp_voltage_v": 52.8,
        "temp_comp_mv_per_c_cell": -5.0,
        "mppt_mode_raw": 0,
        "aux_function_word": 0,
    }
    fields.update(overrides)
    return ClassicChargeSettings(**fields)


def make_battery_with_cvl(cvl: float):
    raw_cvl = round(cvl * 10)
    return decode_pylon_snapshot(
        [
            CanFrame(0x351, bytes([raw_cvl & 0xFF, raw_cvl >> 8, 0xD0, 0x07, 0xD0, 0x07, 0xC0, 0x01])),
            CanFrame(0x355, bytes.fromhex("5C00640000000000")),
            CanFrame(0x356, bytes.fromhex("B814D8FFA4000000")),
        ]
    )


class WebDisplayTest(unittest.TestCase):
    def test_detects_kindle_user_agent(self) -> None:
        self.assertTrue(is_kindle_user_agent("Mozilla/5.0 (X11; U; Linux armv7l) AppleWebKit Kindle/3.0"))
        self.assertTrue(is_kindle_user_agent("Mozilla/5.0 Silk/3.13 like Chrome"))
        self.assertFalse(is_kindle_user_agent("curl/8.0"))

    def test_renders_kindle_snapshot_sections_and_values(self) -> None:
        snapshot = make_snapshot(
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x351, bytes.fromhex("4802D007D007C001")),
                    CanFrame(0x355, bytes.fromhex("6100640000000000")),
                    CanFrame(0x356, bytes.fromhex("51151A00A7000000")),
                    CanFrame(0x359, bytes.fromhex("0000000002504E00")),
                    CanFrame(0x35C, bytes.fromhex("C000000000000000")),
                    CanFrame(0x373, bytes.fromhex("4C0D5A0D21012201")),
                ]
            ),
            magnum=make_magnum_snapshot(),
            disabled_devices=frozenset(["classic", "epever"]),
        )

        html = render_kindle_snapshot(
            snapshot,
            load_summary=LoadSummary(
                current_a=5.1,
                power_w=272,
                average_today_text="3.2A  169W",
                today_text="5.8kWh 106Ah",
                remaining_text="18.7h",
            ),
        )

        details_html = render_kindle_details(snapshot)

        # Page layout, styling, sectioning, and decoded values live in the golden
        # frames; re-bless with UPDATE_GOLDEN=1 on an intended change.
        check_golden(self, "kindle_snapshot", html)
        check_golden(self, "kindle_details", details_html)

        # Behavioral invariants kept explicit so a golden re-bless can't lose
        # them: the wall display refreshes in place (never navigates) on an
        # adaptive cadence, and the page-turn links wire the Kindle's buttons.
        self.assertIn("XMLHttpRequest", html)
        self.assertIn("LIVE_MS = 60000", html)
        self.assertIn("RETRY_MS = 5000", html)
        self.assertNotIn('http-equiv="refresh"', html)
        self.assertIn('class="page-turn page-turn-left" href="/kindle/weather"', html)
        self.assertIn('class="page-turn page-turn-right" href="/kindle/details"', html)
        self.assertIn('class="page-turn page-turn-left" href="/kindle"', details_html)
        self.assertIn('class="page-turn page-turn-right" href="/kindle/weather"', details_html)

    def test_kindle_snapshot_hides_untrusted_cc1_temperature_rows(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(),
            epever=make_epever_telemetry(battery_temp_c=0.0, device_temp_c=0.0),
        )

        html = render_kindle_details(snapshot)

        self.assertIn("Battery terminal", html)
        self.assertIn("CC0 FET", html)
        self.assertNotIn("CC1 battery", html)
        self.assertNotIn("CC1 device", html)

    def test_renders_kindle_snapshot_with_empty_and_error_snapshots(self) -> None:
        # Degraded states must render, not raise: this is what the wall
        # display shows when sensors drop out.
        empty_html = render_kindle_snapshot(make_snapshot())
        self.assertIn("No data", empty_html)
        self.assertIn("SOC --", empty_html)
        self.assertNotIn("SOC SOC", empty_html)
        # No errors and no conditions -> the Warnings and Faults group is omitted
        # entirely on the Kindle (no standalone "none" row).
        self.assertNotIn("<h2>Warnings and Faults</h2>", empty_html)

        # Errors fold into the Warnings and Faults group (no separate Errors group),
        # and the group must not also say "none" when an error is present.
        error_html = render_kindle_snapshot(make_snapshot(errors=["Battery CAN read failed: timeout"]))
        self.assertNotIn("<h2>Errors</h2>", error_html)
        self.assertIn("<h2>Warnings and Faults</h2>", error_html)
        self.assertIn("Battery CAN read failed: timeout", error_html)
        self.assertNotIn('<td colspan="2">none</td>', error_html)

    def test_renders_kindle_weather_values(self) -> None:
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc),
            data={
                "current": {
                    "temperature_2m": 12.4,
                    "weather_code": 61,
                    "wind_speed_10m": 18,
                    "wind_gusts_10m": 32,
                    "wind_direction_10m": 250,
                },
                "daily": {
                    "time": ["2026-06-06"],
                    "weather_code": [61],
                    "temperature_2m_min": [8.2],
                    "temperature_2m_max": [14.8],
                    "precipitation_probability_max": [70],
                    "precipitation_sum": [3.4],
                    "sunrise": ["2026-06-06T05:39"],
                    "sunset": ["2026-06-06T21:37"],
                    "moon_phase": [0.72],
                },
            },
        )

        # Fixed now so the staleness-dependent render is deterministic.
        html = render_kindle_weather(
            weather_api_payload(report), now=datetime(2026, 6, 6, 14, 31, tzinfo=timezone.utc)
        )

        # Layout + derived formatting (code→text, wind direction, moon name) in
        # the golden; behavioral cadence/nav kept explicit.
        check_golden(self, "kindle_weather", html)
        self.assertIn("XMLHttpRequest", html)
        self.assertIn("offgrid-live", html)  # live page → slow cadence
        self.assertIn('class="page-turn page-turn-left" href="/kindle/details"', html)
        self.assertIn('class="page-turn page-turn-right" href="/kindle"', html)

    def test_renders_weather_unavailable_with_retry(self) -> None:
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc),
            data={},
            stale=True,
            error="network unavailable",
        )

        html = render_kindle_weather(weather_api_payload(report))

        self.assertIn("Weather unavailable", html)
        self.assertIn("network unavailable", html)
        self.assertIn("XMLHttpRequest", html)

    def test_renders_browser_weather_dark_terminal_flow(self) -> None:
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc),
            data={
                "current": {
                    "temperature_2m": 12.4,
                    "apparent_temperature": 11.8,
                    "relative_humidity_2m": 70,
                    "weather_code": 61,
                    "wind_speed_10m": 18,
                    "wind_gusts_10m": 32,
                    "wind_direction_10m": 250,
                }
            },
        )

        html = render_browser_weather(weather_api_payload(report))

        # Dark-terminal layout/styling/values in the golden.
        check_golden(self, "browser_weather", html)

        # Behavioral: the browser page refreshes in place on the slow live
        # cadence and uses the <pre> terminal style, not the old table markup.
        self.assertIn("var LIVE_MS = 300000, RETRY_MS = 5000;", html)
        self.assertIn("XMLHttpRequest", html)
        self.assertIn("<pre>Current", html)
        self.assertNotIn("<tr><th>Metric</th><th>Value</th></tr>", html)

    def test_hides_weather_details_after_stale_cutoff(self) -> None:
        fetched_at = datetime(2026, 6, 6, 14, 30, tzinfo=timezone.utc)
        report = WeatherReport(
            label="Cabin",
            fetched_at=fetched_at,
            data={"current": {"temperature_2m": 12.4}},
            stale=True,
            error="network unavailable",
        )

        html = render_kindle_weather(weather_api_payload(report), now=fetched_at + timedelta(hours=1, minutes=1))

        self.assertIn("Weather service has been unreachable since", html)
        self.assertNotIn("12.4C", html)

    def test_protection_text_helper(self) -> None:
        self.assertIsNone(_protection_text(None, None))
        self.assertEqual(_protection_text(True, False), "GFP on  Arc off")
        self.assertEqual(_protection_text(True, True), "GFP on  Arc on")

    def test_kindle_omits_protection_row_even_when_armed(self) -> None:
        # The Protection row is intentionally kept off the cramped wall display,
        # even when the enable bits are known -- it lives on the API/other views.
        snapshot = make_snapshot(
            classic=make_classic_telemetry(
                ground_fault_protection_enabled=True,
                arc_fault_protection_enabled=True,
            ),
        )

        html = render_kindle_snapshot(snapshot)
        self.assertNotIn("Protection", html)

        # ...but the state is still exposed in the API payload.
        payload = snapshot_api_payload(snapshot)
        classic = next(c for c in payload["solar"] if c["id"].startswith("classic"))
        self.assertEqual(classic["protection_enabled"]["arc_fault"], True)
        self.assertEqual(classic["protection_text"], "GFP on  Arc on")

    def test_renders_redundant_charge_controller_state_only_once(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(charge_stage="Resting", state="Resting"),
        )

        html = render_kindle_snapshot(snapshot)

        # Phase and activity both "Resting" fuse to one dense token; the verbose
        # "Stage:"/"State:" scaffolding is gone.
        self.assertIn("Resting", html)
        self.assertNotIn("Stage:", html)
        self.assertNotIn("State:", html)

    def test_renders_epever_charge_controller_group(self) -> None:
        snapshot = make_snapshot(
            epever=make_epever_telemetry(generated_today_kwh=0.12),
            epever_settings=make_epever_settings(
                equalize_voltage_v=54.7,
                charging_limit_voltage_v=60.0,
            ),
            # Classic is not installed on this system — mark it disabled so the
            # renderer doesn't inject an UNREACHABLE stub and shift EPEver's index.
            disabled_devices=frozenset(["classic"]),
        )

        html = render_kindle_snapshot(snapshot)

        # Only an EPEver is present, so it is index 0 in the collection: the
        # renderer numbers by position, not by a fixed per-vendor slot.
        self.assertIn("<h2>Charge Controller 0 (Epever)</h2>", html)
        self.assertIn("53.1V  0.0A  0W", html)
        # EPEver "No charging" canonicalizes fully to a bare "Resting" (the
        # native word is a pure synonym, not a lossy distinction like Boost).
        self.assertIn("Resting", html)
        self.assertNotIn("No charging", html)
        # Charge Settings now live in the per-controller group on the main page.
        self.assertIn("Charge Settings", html)
        self.assertIn("80.0A Abs 54.7V/120m Flt 53.6V", html)
        self.assertNotIn("EQ 54.7V", html)
        # cc group mirrors the Classic: daily generation as "Production Today",
        # and no static "Rated" line.
        self.assertIn("Production Today", html)
        self.assertNotIn("Rated", html)

        # The details page no longer carries a separate Charge Controller
        # Settings section -- the settings moved into the main controller groups.
        details_html = render_kindle_details(snapshot)
        self.assertNotIn("<h2>Charge Controller Settings</h2>", details_html)
        self.assertNotIn("80.0A Abs 54.7V/120m Flt 53.6V", details_html)

    def test_kindle_charge_controllers_render_allocation_rows(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(),
            epever=make_epever_telemetry(),
        )
        allocation = {
            "targets": {
                "classic": {
                    "target_a": 37.0,
                    "disable": False,
                    "should_write": True,
                    "reason": "BMS CCL fraction",
                },
                "epever": {
                    "target_a": 100.0,
                    "disable": False,
                    "should_write": False,
                    "reason": "charger inactive",
                },
            }
        }

        html = render_kindle_snapshot(snapshot, allocation=allocation)

        self.assertIn("<td>Allocation</td><td>limited 37.0A *</td>", html)
        self.assertIn("<td>Allocation</td><td>released</td>", html)

    def test_kindle_charge_controller_allocation_row_renders_hard_stop(self) -> None:
        snapshot = make_snapshot(epever=make_epever_telemetry())

        html = render_kindle_snapshot(
            snapshot,
            allocation={"targets": {"epever": {"target_a": 0.0, "disable": True, "should_write": True}}},
        )

        self.assertIn("<td>Allocation</td><td>off *</td>", html)

    def test_routes_kindle_with_allocation_payload(self) -> None:
        snapshot = make_snapshot(classic=make_classic_telemetry())

        response = route_display_request(
            snapshot,
            "/kindle",
            "Kindle/3.0",
            allocation={"targets": {"classic": {"target_a": 80.0, "reason": "unconstrained"}}},
        )

        self.assertEqual(response.status.value, 200)
        self.assertIn(b"<td>Allocation</td><td>released</td>", response.body)

    def test_renders_bms_protections_as_status_conditions(self) -> None:
        # Protections/alarms appear in the Warnings and Faults group, not a passive
        # battery "Protection/Alarms" row.
        snapshot = make_snapshot(
            battery=PylonCanSnapshot(
                status=PylonStatus(
                    module_count=2,
                    protection_flags=("high cell voltage",),
                    alarm_flags=("charge over current",),
                    manufacturer_marker="PN",
                )
            ),
            status_conditions=["BMS protection: high cell voltage", "BMS alarm: charge over current"],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("<h2>Warnings and Faults</h2>", html)
        self.assertIn("BMS protection: high cell voltage", html)
        self.assertIn("BMS alarm: charge over current", html)
        self.assertNotIn("Protection/Alarms", html)

    def test_escapes_error_text(self) -> None:
        html = render_kindle_snapshot(make_snapshot(errors=["bad <device>"]))

        self.assertIn("bad &lt;device&gt;", html)
        self.assertNotIn("bad <device>", html)

    def test_renders_status_conditions(self) -> None:
        snapshot = make_snapshot(
            status_conditions=[
                "Charge controller 0 CVS exceeds battery CVL: Absorb 56.0V > 55.8V",
                "Battery temp low",
            ],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("Status: WARNING", html)
        self.assertIn("<h2>Warnings and Faults</h2>", html)
        self.assertIn(
            '<td colspan="2">Charge controller 0 CVS exceeds battery CVL: Absorb 56.0V &gt; 55.8V; Battery temp low</td>',
            html,
        )
        self.assertEqual(html.count("<h2>Warnings and Faults</h2>"), 1)

        # Status Conditions are shown on the main /kindle page only; the details
        # page no longer repeats them (it still surfaces Errors separately).
        details_html = render_kindle_details(snapshot)
        self.assertNotIn("<h2>Warnings and Faults</h2>", details_html)

    def test_routes_kindle_path(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()

        response = route_display_request(snapshot, "/", "Kindle/3.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Off-Grid Power", response.body)

    def test_routes_regular_browser_to_terminal_style_full_snapshot(self) -> None:
        captured_at = datetime(2026, 6, 13, 12, 30, tzinfo=timezone.utc)
        snapshot = make_snapshot(
            captured_at=captured_at,
            battery=make_battery_snapshot(soc_percent=92),
            classic=make_classic_telemetry(),
            epever=make_epever_telemetry(),
            magnum=make_magnum_snapshot(),
        )

        response = route_display_request(snapshot, "/", "Mozilla/5.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b'class="browser-summary power-summary"', response.body)
        self.assertIn(b'<div class="primary-cell">SOC 92%</div>', response.body)
        self.assertIn(b'<div class="meta-cell">Updated:', response.body)
        self.assertIn(b'<div class="button-cell"><a class="nav-button" href="/weather">Weather</a>', response.body)
        # Viewport meta keeps iOS Safari from rendering at its 980px default and
        # scaling the whole page down (tiny text, big right-hand gutter), and
        # arms the narrow-screen media query.
        self.assertIn(b'<meta name="viewport" content="width=device-width, initial-scale=1">', response.body)
        self.assertIn(b"@media (max-width:480px)", response.body)
        self.assertIn(b"grid-template-columns:24ch minmax(0,1fr) auto", response.body)
        self.assertIn(b"var LIVE_MS = 30000, RETRY_MS = 5000;", response.body)
        self.assertIn(b"XMLHttpRequest", response.body)
        self.assertIn(b"<pre>", response.body)
        self.assertIn(b"Charge Controller 0 (Classic)", response.body)
        self.assertIn(b"Inverter/Charger", response.body)
        self.assertIn(b"Temperatures", response.body)
        self.assertIn(b'href="/weather">Weather</a>', response.body)
        self.assertNotIn(b"Off-Grid Power Supervisor", response.body)
        self.assertNotIn(b"nav-hint", response.body)  # no Kindle footer nav in the browser view

    def test_kindle_paths_always_serve_kindle_content_regardless_of_user_agent(self) -> None:
        # The /kindle* paths are the Kindle interface and must render
        # Kindle-formatted HTML even for a non-Kindle user-agent -- the wall
        # Kindle's browser does not reliably advertise a recognizable UA.
        snapshot = make_snapshot(
            classic=make_classic_telemetry(),
            epever=make_epever_telemetry(),
            magnum=make_magnum_snapshot(),
        )
        browser_ua = "Version/18.0 Mobile/15E148 Safari/604.1"

        for path in ("/kindle", "/kindle/details"):
            with self.subTest(path=path):
                response = route_display_request(snapshot, path, browser_ua)

                self.assertEqual(response.status.value, 200)
                self.assertEqual(response.content_type, "text/html; charset=utf-8")
                # Kindle markup (page-turn tap zones), not the browser <pre> view.
                self.assertNotIn(b"<pre>", response.body)
                self.assertIn(b"page-turn", response.body)
                # The viewport meta is browser-only; the 2011 Kindle WebKit must
                # not get it.
                self.assertNotIn(b'name="viewport"', response.body)

        main = route_display_request(snapshot, "/kindle", browser_ua).body
        details = route_display_request(snapshot, "/kindle/details", browser_ua).body
        self.assertIn(b"<h2>Charge Controller 0 (Classic)</h2>", main)
        self.assertIn(b"<h2>Temperatures</h2>", details)

    def test_kindle_weather_path_always_serves_kindle_weather(self) -> None:
        # /kindle/weather renders the Kindle weather page for any user-agent, so
        # the whole Kindle interface is viewable/testable from a generic browser.
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=80))
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc),
            data={"current": {"temperature_2m": 12.0, "weather_code": 1}},
        )

        response = route_display_request(
            snapshot, "/kindle/weather", "Mozilla/5.0", weather_report=report
        )

        self.assertEqual(response.status.value, 200)
        self.assertNotIn(b"<pre>", response.body)  # Kindle markup, not browser view
        self.assertIn(b'class="page-turn page-turn-right" href="/kindle"', response.body)

    def test_routes_regular_browser_weather_to_dark_terminal_page(self) -> None:
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=92))
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 13, 12, 30, tzinfo=timezone.utc),
            data={"current": {"temperature_2m": 11.0, "weather_code": 3}},
        )

        response = route_display_request(snapshot, "/weather", "Mozilla/5.0", weather_report=report)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b"background:#111", response.body)
        self.assertIn(b'class="browser-summary weather-summary"', response.body)
        self.assertIn(b'<div class="primary-cell">11.0C</div>', response.body)
        self.assertIn(b'<meta name="viewport" content="width=device-width, initial-scale=1">', response.body)
        self.assertIn(b"@media (max-width:480px)", response.body)
        self.assertIn(b"grid-template-columns:24ch minmax(0,1fr) auto", response.body)
        self.assertIn(b'<a class="nav-button" href="/">Power</a>', response.body)
        self.assertIn(b"var LIVE_MS = 300000, RETRY_MS = 5000;", response.body)
        self.assertIn(b"XMLHttpRequest", response.body)
        self.assertIn(b"<pre>Current", response.body)
        self.assertIn(b"Condition", response.body)
        self.assertIn(b"Temperature", response.body)
        self.assertNotIn(b"<tr><th>Metric</th><th>Value</th></tr>", response.body)
        self.assertNotIn(b"Forecast        forecast", response.body)
        self.assertNotIn(b"page-turn", response.body)

    def test_routes_kindle_weather_to_kindle_page(self) -> None:
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=92))
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 13, 12, 30, tzinfo=timezone.utc),
            data={"current": {"temperature_2m": 11.0, "weather_code": 3}},
        )

        response = route_display_request(snapshot, "/weather", "Kindle/3.0", weather_report=report)

        self.assertEqual(response.status.value, 200)
        self.assertIn(b"offgrid-live", response.body)
        self.assertIn(b'page-turn page-turn-left', response.body)
        self.assertIn(b'href="/kindle">Power</a>', response.body)

    def test_routes_kindle_details_path(self) -> None:
        snapshot = make_snapshot(magnum=make_magnum_snapshot())

        response = route_display_request(snapshot, "/kindle/details", "Kindle/3.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Off-Grid Power Details", response.body)
        self.assertIn(b"Inverter/Charger", response.body)
        self.assertIn(b"Weather", response.body)
        self.assertIn(b"&lt; BACK", response.body)

    def test_routes_api_snapshot_as_json(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(),
            epever=make_epever_telemetry(),
            epever_settings=make_epever_settings(),
            battery=make_battery_snapshot(soc_percent=92),
        )

        response = route_display_request(
            snapshot,
            "/api/v1/snapshot",
            "curl/8.0",
            load_summary=LoadSummary(
                current_a=4.0,
                power_w=212,
                remaining_text="46.0h",
                rolling_average_a=3.5,
                rolling_average_w=184.0,
            ),
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "application/json; charset=utf-8")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["site_id"], "cabin")
        self.assertEqual(payload["status"]["severity"], "OK")
        self.assertEqual(payload["battery"]["soc_percent"], 92)
        self.assertAlmostEqual(payload["battery"]["voltage_v"], 53.04)
        self.assertEqual([controller["id"] for controller in payload["solar"]], ["classic.0", "epever.1"])
        # Vendor/model identity is its own block, separate from the parameters.
        self.assertEqual(
            payload["solar"][0]["device"],
            {"vendor": "MidNite", "model": "Classic 200", "short_name": "Classic"},
        )
        self.assertEqual(
            payload["solar"][1]["device"],
            {"vendor": "EPEver", "model": "TEP10425", "short_name": "Epever"},
        )
        self.assertEqual(payload["solar"][0]["conditions"], [])
        self.assertEqual(payload["solar"][0]["daily_amp_hours_ah"], 108)
        self.assertIsNone(payload["solar"][0]["settings"])
        self.assertEqual(payload["solar"][1]["battery_voltage_v"], 53.11)
        self.assertEqual(payload["solar"][1]["settings"]["battery_type"], "User")
        self.assertEqual(payload["solar"][1]["settings"]["bulk_recovery_voltage_v"], 53.6)
        self.assertEqual(payload["solar"][1]["settings"]["absorb_time_minutes"], 120)
        self.assertEqual(payload["solar"][1]["settings"]["equalize_time_minutes"], 10)
        self.assertEqual(payload["load"]["estimated_autonomy_hours"], 46.0)

    def test_routes_api_weather_as_json(self) -> None:
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=92))
        report = WeatherReport(
            label="Cabin",
            fetched_at=datetime(2026, 6, 13, 12, 30, tzinfo=timezone.utc),
            data={"current": {"temperature_2m": 11.0, "weather_code": 3, "wind_direction_10m": 225}},
        )

        response = route_display_request(snapshot, "/api/v1/weather", "curl/8.0", weather_report=report)
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "application/json; charset=utf-8")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["label"], "Cabin")
        self.assertFalse(payload["stale"])
        # Normalized, source-agnostic schema: units in keys, derivations applied.
        self.assertEqual(payload["current"]["temperature_c"], 11.0)
        self.assertEqual(payload["current"]["condition"], {"code": 3, "text": "overcast"})
        self.assertEqual(payload["current"]["wind"]["compass"], "SW")
        self.assertNotIn("data", payload)

    def test_refresh_param_invokes_hook_and_still_returns_current_snapshot(self) -> None:
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=92))
        calls = []

        response = route_display_request(
            snapshot, "/api/v1/snapshot?refresh=1", "curl/8.0", refresh_hook=lambda: calls.append(1)
        )

        # The hook fired (queues a re-poll) but the response is the current
        # snapshot, served without waiting on any source.
        self.assertEqual(calls, [1])
        self.assertEqual(response.status.value, 200)
        self.assertEqual(json.loads(response.body)["battery"]["soc_percent"], 92)

    def test_source_and_weather_refresh_hooks_are_routed_separately(self) -> None:
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=92))
        source, weather = [], []
        hooks = dict(refresh_hook=lambda: source.append(1), weather_refresh_hook=lambda: weather.append(1))

        # No flag: neither hook fires.
        route_display_request(snapshot, "/api/v1/snapshot", "curl/8.0", **hooks)
        self.assertEqual((source, weather), ([], []))

        # Snapshot refresh re-polls sources only.
        route_display_request(snapshot, "/api/v1/snapshot?refresh=1", "curl/8.0", **hooks)
        self.assertEqual((source, weather), ([1], []))

        # Weather refresh re-fetches the forecast only, not the power sources.
        route_display_request(snapshot, "/api/v1/weather?refresh=1", "curl/8.0", **hooks)
        self.assertEqual((source, weather), ([1], [1]))

    def test_wants_source_and_weather_refresh_paths(self) -> None:
        self.assertTrue(wants_source_refresh("/api/v1/snapshot?refresh=1"))
        self.assertTrue(wants_source_refresh("/kindle?refresh=1"))
        self.assertTrue(wants_source_refresh("/kindle/details?refresh=1"))
        self.assertFalse(wants_source_refresh("/api/v1/snapshot"))
        self.assertFalse(wants_source_refresh("/api/v1/weather?refresh=1"))
        self.assertTrue(wants_weather_refresh("/api/v1/weather?refresh=1"))
        self.assertTrue(wants_weather_refresh("/weather?refresh=1"))
        self.assertFalse(wants_weather_refresh("/api/v1/snapshot?refresh=1"))
        self.assertFalse(wants_weather_refresh("/healthz?refresh=1"))

    def test_routes_api_weather_handles_missing_report(self) -> None:
        snapshot = make_snapshot(battery=make_battery_snapshot(soc_percent=92))

        response = route_display_request(snapshot, "/api/v1/weather", "curl/8.0", weather_report=None)
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["stale"])
        self.assertIsNone(payload["current"])
        self.assertEqual(payload["error"], "weather unavailable")

    def test_snapshot_api_payload_includes_charge_controller_settings(self) -> None:
        captured_at = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        snapshot = make_snapshot(
            captured_at=captured_at,
            classic=make_classic_telemetry(captured_at=captured_at),
            classic_settings=ClassicChargeSettings(
                captured_at=captured_at,
                battery_current_limit_a=80.0,
                absorb_voltage_v=55.2,
                float_voltage_v=54.0,
                equalize_voltage_v=55.2,
                sliding_current_limit_a=800,
                absorb_time_s=300,
                max_temp_comp_voltage_v=55.2,
                min_temp_comp_voltage_v=52.8,
                temp_comp_mv_per_c_cell=-5.0,
                mppt_mode_raw=0,
                aux_function_word=0,
            ),
            battery=make_battery_snapshot(soc_percent=92),
        )

        payload = snapshot_api_payload(snapshot)

        self.assertEqual(payload["solar"][0]["settings"]["current_limit_a"], 80.0)
        self.assertEqual(payload["solar"][0]["settings"]["absorb_voltage_v"], 55.2)
        self.assertEqual(payload["solar"][0]["settings"]["absorb_time_minutes"], 5)

    def test_healthz_is_liveness_only_and_stays_ok_with_offline_device(self) -> None:
        # A device read failure must not fail the liveness probe: the supervisor
        # is still running, so restarting it would not help.
        snapshot = make_snapshot(errors=["EPEver read failed: timeout"])

        response = route_display_request(snapshot, "/healthz", "curl/8.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.body, b"ok\n")

    def test_api_health_offline_device_is_degraded_not_error(self) -> None:
        snapshot = make_snapshot(errors=["EPEver read failed: timeout"])

        response = route_display_request(snapshot, "/api/v1/health", "curl/8.0")
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)  # degraded, not down
        self.assertEqual(payload["status"], "WARNING")
        self.assertTrue(payload["ok"])  # degraded is still "up", just not perfect
        self.assertEqual(payload["errors"], ["EPEver read failed: timeout"])
        self.assertEqual(
            payload["checks"]["epever"],
            {"status": "error", "reason": "no_response", "detail": "EPEver read failed: timeout"},
        )
        # A device with no telemetry and no error reads as offline, not error.
        self.assertEqual(
            payload["checks"]["classic"], {"status": "offline", "reason": "no_data", "detail": None}
        )

    def test_health_checks_distinguish_absent_adapter_from_silent_device(self) -> None:
        snapshot = make_snapshot(
            errors=[
                "EPEver read failed: Modbus Error: No response received after 3 retries",
                "Magnum read failed: Could not open /dev/magnum-rs485",
            ]
        )

        checks = json.loads(route_display_request(snapshot, "/api/v1/health", "curl/8.0").body)["checks"]

        # Port opened, device silent -> no_response (remote device absent/unresponsive).
        self.assertEqual(checks["epever"]["reason"], "no_response")
        # Serial node missing -> transport_absent (adapter unplugged).
        self.assertEqual(checks["magnum"]["reason"], "transport_absent")
        # No telemetry, no error captured -> no_data.
        self.assertEqual(checks["classic"]["reason"], "no_data")

    def test_health_checks_report_unconfigured_device_as_disabled(self) -> None:
        # An adapter that isn't configured at all is "disabled", not "offline":
        # no read was attempted, so "no data" would be misleading.
        snapshot = make_snapshot(disabled_devices=frozenset({"magnum"}))

        checks = json.loads(route_display_request(snapshot, "/api/v1/health", "curl/8.0").body)["checks"]

        self.assertEqual(checks["magnum"], {"status": "disabled", "reason": "disabled", "detail": None})
        # A configured-but-silent device still reads as offline/no_data.
        self.assertEqual(checks["classic"]["status"], "offline")

    def test_api_health_critical_condition_returns_service_unavailable(self) -> None:
        snapshot = make_snapshot(
            status_conditions=["Battery cell overvoltage"], status_severity=STATUS_ERROR
        )

        response = route_display_request(snapshot, "/api/v1/health", "curl/8.0")
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 503)
        self.assertEqual(payload["status"], "ERROR")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["conditions"], ["Battery cell overvoltage"])

    def test_snapshot_api_payload_includes_status_conditions(self) -> None:
        snapshot = make_snapshot(
            status_conditions=["Charge controller 0 CVS exceeds battery CVL"],
            disabled_devices=frozenset(["classic", "epever"]),
        )

        payload = snapshot_api_payload(snapshot)

        self.assertEqual(payload["status"]["severity"], "WARNING")
        self.assertEqual(payload["status"]["conditions"], ["Charge controller 0 CVS exceeds battery CVL"])
        self.assertIsNone(payload["battery"])
        self.assertEqual(payload["solar"], [])

    def test_control_api_routes_classic_absorb_time(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/classic/charge-settings",
            {"absorb_time_minutes": 30},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(supervisor.classic_calls, [{"absorb_time_s": 1800}])
        self.assertEqual(payload["settings"]["absorb_time_minutes"], 30)

    def test_control_api_rejects_classic_voltage_above_bms_cvl(self) -> None:
        supervisor = FakeControlSupervisor(snapshot=make_snapshot(
            classic_settings=make_classic_settings(),
            battery=make_battery_with_cvl(55.8),
        ))

        response = route_control_request(
            supervisor,
            "/api/v1/control/classic/charge-settings",
            {"absorb_voltage_v": 55.9},
        )

        self.assertEqual(response.status.value, 400)
        self.assertEqual(supervisor.classic_calls, [])

    def test_control_api_routes_scalar_voltage_to_classic(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "voltage_v": 56.3},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["device"], "classic")
        self.assertEqual(
            supervisor.classic_calls,
            [
                {
                    "absorb_voltage_v": 56.3,
                    "float_voltage_v": 56.2,
                    "equalize_voltage_v": 56.3,
                    "max_temp_comp_voltage_v": 56.3,
                }
            ],
        )
        self.assertEqual(payload["planned"]["float_voltage_v"], 56.2)
        self.assertEqual(payload["settings"]["max_temp_comp_voltage_v"], 56.3)

    def test_control_api_routes_scalar_voltage_to_epever(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"charge_controller_number": 1, "voltage_v": 56.4},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["device"], "epever")
        self.assertEqual(
            supervisor.voltage_calls,
            [
                {
                    "boost_v": 56.4,
                    "float_v": 56.4,
                    "equalize_v": 56.4,
                    "boost_reconnect_v": 55.4,
                }
            ],
        )
        self.assertEqual(payload["planned"]["bulk_recovery_voltage_v"], 55.4)
        self.assertEqual(payload["settings"]["boost_reconnect_voltage_v"], 55.4)

    def test_control_api_scalar_voltage_dry_run_does_not_write(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "voltage_v": 56.3, "dry_run": True},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(supervisor.classic_calls, [])
        self.assertIsNone(payload["settings"])

    def test_control_api_rejects_unknown_charge_controller_number(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/charge-controller/voltage",
            {"controller": 2, "voltage_v": 56.3},
        )

        self.assertEqual(response.status.value, 400)
        self.assertIn("unknown charge controller number", json.loads(response.body)["error"])

    def test_control_api_nudges_classic_scalar_voltage_up(self) -> None:
        supervisor = FakeControlSupervisor()  # Classic absorb starts at 55.2

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "delta_v": 0.1},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(payload["previous_voltage_v"], 55.2)
        self.assertEqual(payload["voltage_v"], 55.3)
        self.assertEqual(payload["delta_v"], 0.1)
        self.assertTrue(payload["confirmed"])
        self.assertEqual(
            supervisor.classic_calls,
            [
                {
                    "absorb_voltage_v": 55.3,
                    "float_voltage_v": 55.2,
                    "equalize_voltage_v": 55.3,
                    "max_temp_comp_voltage_v": 55.3,
                }
            ],
        )

    def test_control_api_nudges_epever_scalar_voltage_down(self) -> None:
        supervisor = FakeControlSupervisor()  # EPEver boost starts at 54.7

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 1, "delta_v": -0.1},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(payload["previous_voltage_v"], 54.7)
        self.assertEqual(payload["voltage_v"], 54.6)
        self.assertEqual(supervisor.voltage_calls, [{"boost_v": 54.6, "float_v": 54.6, "equalize_v": 54.6, "boost_reconnect_v": 53.6}])

    def test_control_api_delta_dry_run_does_not_write(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "delta_v": 0.1, "dry_run": True},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(payload["voltage_v"], 55.3)
        self.assertIsNone(payload["confirmed"])
        self.assertEqual(supervisor.classic_calls, [])

    def test_control_api_delta_guarded_against_bms_cvl(self) -> None:
        # CVL is 58.4; nudging Classic (55.2) up 0.1 is fine, but a delta that
        # would push the absorb target above CVL must be refused.
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "delta_v": 0.95},  # 55.2 + 0.95 = 56.15, under cap, under CVL
        )
        self.assertEqual(response.status.value, 200)

        supervisor = FakeControlSupervisor(
            snapshot=make_snapshot(
                classic_settings=make_classic_settings(absorb_voltage_v=58.35),
                epever_settings=make_epever_settings(charging_limit_voltage_v=60.0),
                battery=make_battery_with_cvl(58.4),
            )
        )
        response = route_control_request(
            supervisor,
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "delta_v": 0.1},  # 58.35 + 0.1 = 58.45 > CVL 58.4
        )
        self.assertEqual(response.status.value, 400)
        self.assertIn("exceeds BMS CVL", json.loads(response.body)["error"])
        self.assertEqual(supervisor.classic_calls, [])

    def test_control_api_rejects_both_voltage_and_delta(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "voltage_v": 56.3, "delta_v": 0.1},
        )
        self.assertEqual(response.status.value, 400)
        self.assertIn("exactly one of", json.loads(response.body)["error"])

    def test_control_api_rejects_oversized_delta(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/charge-controller/voltage",
            {"controller": 0, "delta_v": 2.0},
        )
        self.assertEqual(response.status.value, 400)
        self.assertIn("per-call cap", json.loads(response.body)["error"])

    def test_control_api_nudges_ccl_scaling_factor(self) -> None:
        ceiling = ChargeCeiling()  # default scaling factor 0.5

        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"delta": 0.05},
            charge_ceiling=ceiling,
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(payload["previous_factor"], 0.5)
        self.assertEqual(payload["factor"], 0.55)
        self.assertEqual(payload["delta"], 0.05)
        self.assertEqual(ceiling.scaling_factor, 0.55)

    def test_control_api_sets_ccl_scaling_factor_absolute(self) -> None:
        ceiling = ChargeCeiling()

        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"factor": 0.6},
            charge_ceiling=ceiling,
        )
        self.assertEqual(response.status.value, 200)
        self.assertEqual(ceiling.scaling_factor, 0.6)

    def test_control_api_ccl_scaling_dry_run_does_not_write(self) -> None:
        ceiling = ChargeCeiling()

        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"delta": 0.05, "dry_run": True},
            charge_ceiling=ceiling,
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(payload["factor"], 0.55)
        self.assertEqual(ceiling.scaling_factor, 0.5)  # unchanged

    def test_control_api_ccl_scaling_rejects_out_of_range_result(self) -> None:
        ceiling = ChargeCeiling()
        ceiling.set_scaling_factor(0.95)

        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"delta": 0.1},  # 0.95 + 0.1 = 1.05 > max 1.0
            charge_ceiling=ceiling,
        )
        self.assertEqual(response.status.value, 400)
        self.assertIn("out of range", json.loads(response.body)["error"])
        self.assertEqual(ceiling.scaling_factor, 0.95)  # unchanged

    def test_control_api_ccl_scaling_rejects_oversized_delta(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"delta": 0.3},
            charge_ceiling=ChargeCeiling(),
        )
        self.assertEqual(response.status.value, 400)
        self.assertIn("per-call cap", json.loads(response.body)["error"])

    def test_control_api_ccl_scaling_rejects_both_fraction_and_delta(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"factor": 0.5, "delta": 0.05},
            charge_ceiling=ChargeCeiling(),
        )
        self.assertEqual(response.status.value, 400)
        self.assertIn("exactly one of", json.loads(response.body)["error"])

    def test_control_api_ccl_scaling_conflict_when_allocation_disabled(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/ccl-scaling-factor",
            {"delta": 0.05},
            charge_ceiling=None,
        )
        self.assertEqual(response.status.value, 409)
        self.assertIn("not enabled", json.loads(response.body)["error"])

    def test_control_api_routes_epever_charge_settings(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/epever/charge-settings",
            {
                "absorb_voltage_v": 55.6,
                "equalize_voltage_v": 55.6,
                "bulk_recovery_voltage_v": 54.9,
                "absorb_time_minutes": 90,
                "max_charging_current_a": 80,
            },
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            supervisor.voltage_calls,
            [{"boost_v": 55.6, "equalize_v": 55.6, "boost_reconnect_v": 54.9}],
        )
        self.assertEqual(supervisor.current_calls, [80.0])
        self.assertEqual(supervisor.time_calls, [{"boost_time_minutes": 90}])
        self.assertEqual(payload["settings"]["bulk_recovery_voltage_v"], 54.9)
        self.assertEqual(payload["settings"]["absorb_voltage_v"], 55.6)
        self.assertEqual(payload["settings"]["absorb_time_minutes"], 90)
        self.assertEqual(payload["settings"]["max_charging_current_a"], 80.0)

    def test_control_api_syncs_epever_from_classic_with_voltage_offset(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor,
            "/api/v1/control/epever/sync-from-classic",
            {"voltage_offset_v": 0.3},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertEqual(supervisor.voltage_calls, [{"boost_v": 55.5, "float_v": 54.3, "equalize_v": 55.5}])
        self.assertEqual(supervisor.current_calls, [80.0])
        self.assertEqual(payload["planned"]["boost_voltage_v"], 55.5)
        self.assertEqual(payload["voltage_offset_v"], 0.3)

    def test_control_api_rejects_epever_voltage_above_bms_cvl(self) -> None:
        supervisor = FakeControlSupervisor(snapshot=make_snapshot(
            epever_settings=make_epever_settings(charging_limit_voltage_v=60.0),
            battery=make_battery_with_cvl(55.8),
        ))

        response = route_control_request(
            supervisor,
            "/api/v1/control/epever/charge-settings",
            {"boost_voltage_v": 55.9, "equalize_voltage_v": 55.9},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 400)
        self.assertIn("exceeds BMS CVL", payload["error"])
        self.assertEqual(supervisor.voltage_calls, [])

    def test_control_api_rejects_sync_offset_above_bms_cvl(self) -> None:
        supervisor = FakeControlSupervisor(snapshot=make_snapshot(
            classic_settings=make_classic_settings(absorb_voltage_v=55.6, equalize_voltage_v=55.6),
            epever_settings=make_epever_settings(charging_limit_voltage_v=60.0),
            battery=make_battery_with_cvl(55.8),
        ))

        response = route_control_request(
            supervisor,
            "/api/v1/control/epever/sync-from-classic",
            {"voltage_offset_v": 0.3},
        )

        self.assertEqual(response.status.value, 400)
        self.assertEqual(supervisor.voltage_calls, [])

    def test_control_api_routes_epever_charging(self) -> None:
        supervisor = FakeControlSupervisor()

        response = route_control_request(
            supervisor, "/api/v1/control/epever/charging", {"enabled": False}
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 200)
        self.assertFalse(payload["enabled"])
        self.assertEqual(supervisor.charge_calls, [False])

    def test_control_api_rejects_bad_epever_payload(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(), "/api/v1/control/epever/charge-settings", {}
        )

        self.assertEqual(response.status.value, 400)
        self.assertIn("no EPEver charge settings", json.loads(response.body)["error"])

    def test_control_api_exposes_magnum_as_not_implemented(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(),
            "/api/v1/control/magnum/charge-settings",
            {"absorb_voltage_v": 54.4},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 501)
        self.assertEqual(payload["reason"], "not_implemented")
        self.assertEqual(payload["device"], "magnum")

    def test_control_api_rejects_magnum_voltage_above_bms_cvl_before_backend(self) -> None:
        response = route_control_request(
            FakeControlSupervisor(snapshot=make_snapshot(battery=make_battery_with_cvl(55.8))),
            "/api/v1/control/magnum/charge-settings",
            {"absorb_voltage_v": 55.9},
        )
        payload = json.loads(response.body)

        self.assertEqual(response.status.value, 400)
        self.assertIn("exceeds BMS CVL", payload["error"])

    def test_snapshot_api_payload_includes_cell_locations(self) -> None:
        snapshot = make_snapshot(
            battery=decode_pylon_snapshot(
                [
                    CanFrame(0x373, bytes.fromhex("4E0DD80D24012501")),
                    CanFrame(0x374, bytes.fromhex("3032313400000000")),
                    CanFrame(0x375, bytes.fromhex("3032313000000000")),
                ]
            ),
        )

        payload = snapshot_api_payload(snapshot)

        self.assertIsNotNone(payload["battery"])
        self.assertEqual(payload["battery"]["cell_min_location"], "02:14")
        self.assertEqual(payload["battery"]["cell_min_pack_number"], 2)
        self.assertEqual(payload["battery"]["cell_min_number"], 14)
        self.assertEqual(payload["battery"]["cell_max_location"], "02:10")
        self.assertEqual(payload["battery"]["cell_max_pack_number"], 2)
        self.assertEqual(payload["battery"]["cell_max_number"], 10)

    def test_snapshot_unavailable_page_auto_refreshes(self) -> None:
        html = render_snapshot_unavailable(RuntimeError("CAN bus warming up"), refresh_seconds=10)

        self.assertIn('<meta http-equiv="refresh" content="10">', html)
        self.assertIn("Snapshot unavailable", html)
        self.assertIn("CAN bus warming up", html)
        # No live sentinel → the wall display's refresher fast-retries until
        # the real dashboard returns, rather than waiting a full slow cycle.
        self.assertNotIn("offgrid-live", html)

    def test_snapshot_cache_returns_latest_snapshot(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()
        cache = SnapshotCache()

        with self.assertRaisesRegex(RuntimeError, "no supervisor snapshot"):
            cache.get()

        cache.set(snapshot)

        self.assertIs(cache.get(), snapshot)
        self.assertIsNone(cache.get_load_summary())

    def test_snapshot_cache_stores_load_summary_with_snapshot(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()
        load_summary = LoadSummary(current_a=5.1, power_w=272, today_text="38.6Ah 19.3% of bank")
        cache = SnapshotCache()

        cache.set(snapshot, load_summary)

        self.assertIs(cache.get_load_summary(), load_summary)


class TestAllocationOverrideAPI(unittest.TestCase):
    def _supervisor(self):
        return FakeControlSupervisor()

    def test_pause_sets_paused(self):
        from offgrid_power.charge_allocator import AllocationOverride
        override = AllocationOverride()
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/pause", {"paused": True},
            allocation_override=override,
        )
        payload = json.loads(response.body)
        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["previous_paused"])
        self.assertTrue(payload["paused"])
        self.assertTrue(override.paused)

    def test_resume_clears_paused(self):
        from offgrid_power.charge_allocator import AllocationOverride
        override = AllocationOverride()
        override.set_paused(True)
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/pause", {"paused": False},
            allocation_override=override,
        )
        payload = json.loads(response.body)
        self.assertTrue(payload["ok"])
        self.assertFalse(override.paused)

    def test_pause_missing_field_is_bad_request(self):
        from offgrid_power.charge_allocator import AllocationOverride
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/pause", {},
            allocation_override=AllocationOverride(),
        )
        self.assertEqual(response.status.value, 400)

    def test_pause_without_override_is_conflict(self):
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/pause", {"paused": True},
            allocation_override=None,
        )
        self.assertEqual(response.status.value, 409)

    def test_manual_limit_sets_ceiling(self):
        from offgrid_power.charge_allocator import AllocationOverride
        override = AllocationOverride()
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/manual-limit",
            {"controller": 0, "limit_a": 0.0},
            allocation_override=override,
        )
        payload = json.loads(response.body)
        self.assertEqual(response.status.value, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["controller"], 0)
        self.assertIsNone(payload["previous_limit_a"])
        self.assertEqual(payload["limit_a"], 0.0)

    def test_manual_limit_clear(self):
        from offgrid_power.charge_allocator import AllocationOverride
        override = AllocationOverride()
        override.set_manual_limit(0, 5.0)
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/manual-limit",
            {"controller": 0, "limit_a": None},
            allocation_override=override,
        )
        payload = json.loads(response.body)
        self.assertEqual(response.status.value, 200)
        self.assertIsNone(payload["limit_a"])

    def test_manual_limit_invalid_index_is_bad_request(self):
        from offgrid_power.charge_allocator import AllocationOverride
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/manual-limit",
            {"controller": 99, "limit_a": 10.0},
            allocation_override=AllocationOverride(),
        )
        self.assertEqual(response.status.value, 400)

    def test_manual_limit_without_override_is_conflict(self):
        response = route_control_request(
            self._supervisor(), "/api/v1/control/allocation/manual-limit",
            {"controller": 0, "limit_a": 0.0},
            allocation_override=None,
        )
        self.assertEqual(response.status.value, 409)


if __name__ == "__main__":
    unittest.main()

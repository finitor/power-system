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
from offgrid_power.classic import ClassicChargeSettings
from offgrid_power.load import LoadSummary
from offgrid_power.supervisor import STATUS_ERROR, Supervisor
from offgrid_power.web_display import (
    SnapshotCache,
    is_kindle_user_agent,
    render_kindle_snapshot,
    render_kindle_weather,
    render_snapshot_unavailable,
    route_display_request,
    snapshot_api_payload,
    wants_source_refresh,
    wants_weather_refresh,
)
from offgrid_power.weather import WeatherReport, weather_api_payload
from snapshot_helpers import make_battery_snapshot, make_classic_telemetry, make_epever_settings, make_epever_telemetry, make_magnum_snapshot, make_snapshot


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

        # The in-place XHR refresher keeps the Kindle wall display alive:
        # it never navigates, so a Pi reboot can't strand the browser on a
        # native error page.
        self.assertIn("XMLHttpRequest", html)
        # Adaptive cadence: slow when live, fast-retry otherwise.
        self.assertIn("LIVE_MS = 60000", html)
        self.assertIn("RETRY_MS = 5000", html)
        self.assertNotIn('http-equiv="refresh"', html)
        # Live-page sentinel drives the slow cadence (and recovery detection).
        self.assertIn("offgrid-live", html)
        self.assertIn("SOC 97%", html)
        # This snapshot has no charge controllers, so the collection renders a
        # single empty group rather than hardcoded per-vendor sections.
        for section in ("Load", "Battery Bank", "Charge Controllers", "Inverter/Charger", "Temperatures"):
            self.assertIn(f"<h2>{section}</h2>", html)
        # Decoded values flow through to the page.
        self.assertIn("5.1A  272W", html)
        self.assertIn("18.7h", html)
        self.assertIn("54.57V", html)
        # Inverter/Charger telemetry renders.
        self.assertIn("60.0Hz", html)
        self.assertIn("Inverting", html)

    def test_renders_kindle_snapshot_with_empty_and_error_snapshots(self) -> None:
        # Degraded states must render, not raise: this is what the wall
        # display shows when sensors drop out.
        empty_html = render_kindle_snapshot(make_snapshot())
        self.assertIn("No data", empty_html)
        self.assertIn("SOC --", empty_html)
        self.assertNotIn("SOC SOC", empty_html)

        error_html = render_kindle_snapshot(make_snapshot(errors=["Battery CAN read failed: timeout"]))
        self.assertIn("<h2>Errors</h2>", error_html)
        self.assertIn("Battery CAN read failed: timeout", error_html)

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

        html = render_kindle_weather(weather_api_payload(report))

        self.assertIn("XMLHttpRequest", html)
        self.assertIn("offgrid-live", html)  # live page → slow cadence
        # Derived formatting: weather code text, wind direction, moon phase name.
        self.assertIn("Cabin: rain", html)
        self.assertIn("18km/h  32km/h gust  W", html)
        self.assertIn("rise 05:39  set 21:37", html)
        self.assertIn("last quarter (0.72)", html)

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

    def test_renders_redundant_charge_controller_state_only_once(self) -> None:
        snapshot = make_snapshot(
            classic=make_classic_telemetry(charge_stage="Resting", state="Resting"),
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("Stage: Resting", html)
        self.assertNotIn("State: Resting", html)

    def test_renders_epever_charge_controller_group(self) -> None:
        snapshot = make_snapshot(
            epever=make_epever_telemetry(),
            epever_settings=make_epever_settings(),
        )

        html = render_kindle_snapshot(snapshot)

        # Only an EPEver is present, so it is index 0 in the collection: the
        # renderer numbers by position, not by a fixed per-vendor slot.
        self.assertIn("<h2>Charge Controller 0 (EPEver TEP10425)</h2>", html)
        self.assertIn("53.1V  0.0A  0W", html)
        # EPEver "No charging" normalizes to canonical Resting, native in parens.
        self.assertIn("Stage: Resting (No charging)", html)
        self.assertIn("Type User  Boost 54.7V  Float 53.6V  LVD 49.7V", html)

    def test_renders_bms_protections_and_alarms(self) -> None:
        snapshot = make_snapshot(
            battery=PylonCanSnapshot(
                status=PylonStatus(
                    module_count=2,
                    protection_flags=("high cell voltage",),
                    alarm_flags=("charge over current",),
                    manufacturer_marker="PN",
                )
            ),
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("high cell voltage, charge over current", html)

    def test_escapes_error_text(self) -> None:
        html = render_kindle_snapshot(make_snapshot(errors=["bad <device>"]))

        self.assertIn("bad &lt;device&gt;", html)
        self.assertNotIn("bad <device>", html)

    def test_renders_status_conditions(self) -> None:
        snapshot = make_snapshot(
            status_conditions=["Charge controller 0 CCL exceeds battery CCL: 80.0A > 40.0A"],
        )

        html = render_kindle_snapshot(snapshot)

        self.assertIn("Status: WARNING", html)
        self.assertIn("<h2>Status Conditions</h2>", html)
        self.assertIn("Charge controller 0 CCL exceeds battery CCL: 80.0A &gt; 40.0A", html)

    def test_routes_kindle_path(self) -> None:
        snapshot = Supervisor(classic=None, ambient=None, battery=None).read_snapshot()

        response = route_display_request(snapshot, "/", "Kindle/3.0")

        self.assertEqual(response.status.value, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertIn(b"Off-Grid Power", response.body)

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
        self.assertEqual(payload["solar"][0]["device"], {"vendor": "MidNite", "model": "Classic 200"})
        self.assertEqual(payload["solar"][1]["device"], {"vendor": "EPEver", "model": "TEP10425"})
        self.assertEqual(payload["solar"][0]["conditions"], [])
        self.assertEqual(payload["solar"][0]["daily_amp_hours_ah"], 108)
        self.assertIsNone(payload["solar"][0]["settings"])
        self.assertEqual(payload["solar"][1]["battery_voltage_v"], 53.11)
        self.assertEqual(payload["solar"][1]["settings"]["battery_type"], "User")
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
        self.assertEqual(payload["solar"][0]["settings"]["absorb_time_s"], 300)

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
        snapshot = make_snapshot(status_conditions=["Battery cell delta high"])

        payload = snapshot_api_payload(snapshot)

        self.assertEqual(payload["status"]["severity"], "WARNING")
        self.assertEqual(payload["status"]["conditions"], ["Battery cell delta high"])
        self.assertIsNone(payload["battery"])
        self.assertEqual(payload["solar"], [])

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


if __name__ == "__main__":
    unittest.main()

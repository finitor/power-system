from __future__ import annotations

from datetime import datetime, timezone
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

from snapshot_helpers import make_snapshot
from offgrid_power.api_terminal_display import render_api_snapshot
from offgrid_power.load import LoadSummary
from offgrid_power.metrics import snapshot_metric_samples
from offgrid_power.supervisor import Supervisor
from offgrid_power.tasmota import TasmotaClient, TasmotaTelemetry
from offgrid_power.terminal_display import render_snapshot
from offgrid_power.web_display import (
    render_browser_snapshot,
    render_kindle_snapshot,
    snapshot_api_payload,
)


STATUS_10 = b'''{"StatusSNS":{"Time":"2026-07-10T12:00:00","ENERGY":{
    "TotalStartTime":"2026-07-10T00:00:00","Total":12.345,
    "Yesterday":1.234,"Today":0.456,"Period":0,"Power":87,
    "ApparentPower":91,"ReactivePower":27,"Factor":0.96,
    "Voltage":121,"Current":0.75}}}'''


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TasmotaClientTest(unittest.TestCase):
    @patch("offgrid_power.tasmota.urlopen", return_value=_Response(STATUS_10))
    def test_reads_status_10_energy_payload(self, urlopen_mock) -> None:
        telemetry = TasmotaClient("refrigeration", "192.168.0.210").read()

        self.assertEqual(telemetry.name, "refrigeration")
        self.assertEqual(telemetry.host, "192.168.0.210")
        self.assertEqual(telemetry.power_w, 87)
        self.assertEqual(telemetry.energy_total_kwh, 12.345)
        request = urlopen_mock.call_args.args[0]
        self.assertIn("cmnd=Status+10", request.full_url)
        self.assertEqual(request.get_header("Referer"), "http://192.168.0.210/")

    def test_supervisor_collects_named_tasmota_device(self) -> None:
        telemetry = make_telemetry()

        class Client:
            def read(self):
                return telemetry

        snapshot = Supervisor(classic=None, tasmota={"refrigeration": Client()}).read_snapshot()

        self.assertEqual(snapshot.tasmota["refrigeration"].power_w, 87)

    def test_metrics_and_api_include_monitored_load(self) -> None:
        telemetry = make_telemetry()
        snapshot = make_snapshot(tasmota={"refrigeration": telemetry})

        samples = list(snapshot_metric_samples(snapshot))
        payload = snapshot_api_payload(snapshot)

        self.assertTrue(any(
            sample.source == "tasmota.refrigeration"
            and sample.metric == "power"
            and sample.value == 87
            and sample.unit == "W"
            for sample in samples
        ))
        self.assertEqual(payload["monitored_loads"][0]["name"], "refrigeration")
        self.assertEqual(payload["monitored_loads"][0]["energy_today_kwh"], 0.456)

    def test_all_load_displays_show_refrigeration_on_its_own_last_row(self) -> None:
        telemetry = make_telemetry()
        snapshot = make_snapshot(tasmota={"refrigeration": telemetry})
        summary = LoadSummary(
            current_a=5.1,
            power_w=272,
            average_today_text="3.2A  169W",
            today_text="5.8kWh 106Ah",
            remaining_text="24.0h",
        )

        direct = render_snapshot(snapshot, load_summary=summary)
        payload = snapshot_api_payload(snapshot, load_summary=summary)
        api_terminal = render_api_snapshot(payload)
        kindle = render_kindle_snapshot(snapshot, load_summary=summary)
        browser = render_browser_snapshot(snapshot, load_summary=summary)

        expected = "Now 87W  3hr 100W  Cumulative 0.5kWh"
        for rendered in (direct, api_terminal, kindle, browser):
            self.assertIn("Refrigeration", rendered)
            self.assertIn(expected, rendered)
            self.assertNotIn("(Refrigeration", rendered)
            self.assertLess(rendered.index("Cumulative Today"), rendered.index("Refrigeration"))
            self.assertLess(rendered.index("Estimated Autonomy"), rendered.index("Refrigeration"))

    def test_all_load_displays_hide_refrigeration_without_a_current_reading(self) -> None:
        snapshot = make_snapshot(tasmota={})
        summary = LoadSummary(current_a=5.1, power_w=272)
        payload = snapshot_api_payload(snapshot, load_summary=summary)

        rendered_displays = (
            render_snapshot(snapshot, load_summary=summary),
            render_api_snapshot(payload),
            render_kindle_snapshot(snapshot, load_summary=summary),
            render_browser_snapshot(snapshot, load_summary=summary),
        )

        for rendered in rendered_displays:
            self.assertNotIn("Refrigeration", rendered)


def make_telemetry() -> TasmotaTelemetry:
    return TasmotaTelemetry(
        captured_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
        name="refrigeration",
        host="192.168.0.210",
        voltage_v=121,
        current_a=0.75,
        power_w=87,
        apparent_power_va=91,
        reactive_power_var=27,
        power_factor=0.96,
        energy_today_kwh=0.456,
        energy_yesterday_kwh=1.234,
        energy_total_kwh=12.345,
        rolling_average_power_w=99.6,
    )


if __name__ == "__main__":
    unittest.main()

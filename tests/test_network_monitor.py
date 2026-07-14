from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "software" / "pi-controller" / "src"))

from offgrid_power.network_monitor import NetworkMonitor  # noqa: E402


class NetworkMonitorLanDebounceTest(unittest.TestCase):
    def test_single_failed_probe_does_not_drop_healthy_lan(self) -> None:
        monitor = NetworkMonitor(lan_failure_threshold=2)
        monitor._observe_lan_probe(True)

        monitor._observe_lan_probe(False)

        self.assertTrue(monitor.lan_reachable)

    def test_consecutive_failures_drop_lan_and_success_restores_immediately(self) -> None:
        monitor = NetworkMonitor(lan_failure_threshold=2)
        monitor._observe_lan_probe(True)

        monitor._observe_lan_probe(False)
        monitor._observe_lan_probe(False)
        self.assertFalse(monitor.lan_reachable)

        monitor._observe_lan_probe(True)
        self.assertTrue(monitor.lan_reachable)

    def test_startup_failure_waits_for_threshold(self) -> None:
        monitor = NetworkMonitor(lan_failure_threshold=2)

        monitor._observe_lan_probe(False)
        self.assertIsNone(monitor.lan_reachable)

        monitor._observe_lan_probe(False)
        self.assertFalse(monitor.lan_reachable)


if __name__ == "__main__":
    unittest.main()

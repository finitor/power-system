from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC = REPO_ROOT / "software" / "pi-controller" / "src"
sys.path.insert(0, str(PACKAGE_SRC))

import os
import select
import signal

from offgrid_power.cli.api_terminal_display import (
    TUNE_SESSION_BUDGET_V,
    VIEW_POWER,
    VIEW_WEATHER,
    TuneState,
    _with_refresh,
    commit_tune,
    compose_frame,
    derive_weather_url,
    footer,
    resize_wakeup,
    resolve_key,
    resolve_tune_key,
    scalars_from_snapshot,
    tune_footer,
)


class DeriveWeatherUrlTest(unittest.TestCase):
    def test_swaps_snapshot_path_for_weather(self) -> None:
        self.assertEqual(
            derive_weather_url("http://127.0.0.1:8081/api/v1/snapshot"),
            "http://127.0.0.1:8081/api/v1/weather",
        )

    def test_leaves_unrecognized_url_untouched(self) -> None:
        self.assertEqual(derive_weather_url("http://host/custom"), "http://host/custom")


class ResolveKeyTest(unittest.TestCase):
    def test_explicit_view_keys(self) -> None:
        self.assertEqual(resolve_key("p", VIEW_WEATHER), VIEW_POWER)
        self.assertEqual(resolve_key("w", VIEW_POWER), VIEW_WEATHER)
        self.assertEqual(resolve_key("W", VIEW_POWER), VIEW_WEATHER)  # case-insensitive

    def test_space_and_tab_toggle(self) -> None:
        self.assertEqual(resolve_key(" ", VIEW_POWER), VIEW_WEATHER)
        self.assertEqual(resolve_key(" ", VIEW_WEATHER), VIEW_POWER)
        self.assertEqual(resolve_key("\t", VIEW_POWER), VIEW_WEATHER)

    def test_quit_key(self) -> None:
        self.assertEqual(resolve_key("q", VIEW_POWER), "quit")
        self.assertEqual(resolve_key("Q", VIEW_WEATHER), "quit")

    def test_unrecognized_key_ignored(self) -> None:
        self.assertIsNone(resolve_key("x", VIEW_POWER))
        self.assertIsNone(resolve_key("\n", VIEW_POWER))


class FooterTest(unittest.TestCase):
    def test_marks_the_active_view(self) -> None:
        power = footer(VIEW_POWER)
        self.assertIn("[p] POWER", power)
        self.assertIn("[w] Weather", power)
        self.assertIn("[q] Quit", power)

        weather = footer(VIEW_WEATHER)
        self.assertIn("[w] WEATHER", weather)
        self.assertIn("[p] Power", weather)

    def test_includes_font_size_reminder(self) -> None:
        # 4-space gap separates the font group from the view controls; single
        # spaces inside the group.
        self.assertIn("[q] Quit    Font ↓F7 ↑F8", footer(VIEW_POWER))


class WithRefreshTest(unittest.TestCase):
    def test_appends_refresh_param(self) -> None:
        self.assertEqual(
            _with_refresh("http://127.0.0.1:8081/api/v1/snapshot"),
            "http://127.0.0.1:8081/api/v1/snapshot?refresh=1",
        )

    def test_uses_ampersand_when_query_present(self) -> None:
        self.assertEqual(_with_refresh("http://host/snapshot?k=1"), "http://host/snapshot?k=1&refresh=1")


class ResizeWakeupTest(unittest.TestCase):
    def test_disabled_yields_none(self) -> None:
        with resize_wakeup(False) as fd:
            self.assertIsNone(fd)

    @unittest.skipUnless(hasattr(signal, "SIGWINCH"), "no SIGWINCH on this platform")
    def test_sigwinch_makes_fd_readable_and_restores_handler(self) -> None:
        before = signal.getsignal(signal.SIGWINCH)
        with resize_wakeup(True) as fd:
            self.assertIsNotNone(fd)
            os.kill(os.getpid(), signal.SIGWINCH)
            ready, _, _ = select.select([fd], [], [], 1.0)
            self.assertIn(fd, ready)
        self.assertEqual(signal.getsignal(signal.SIGWINCH), before)


class ComposeFrameTest(unittest.TestCase):
    def test_pins_footer_to_bottom_row(self) -> None:
        frame = compose_frame("line1\nline2", "FOOTER", height=6)
        rows = frame.split("\n")

        self.assertEqual(len(rows), 6)  # exactly fills the pane height
        self.assertEqual(rows[0], "line1")
        self.assertEqual(rows[1], "line2")
        self.assertEqual(rows[2:5], ["", "", ""])  # blank gap
        self.assertEqual(rows[-1], "FOOTER")
        self.assertFalse(frame.endswith("\n"))  # no trailing newline -> no scroll

    def test_truncates_body_taller_than_pane(self) -> None:
        body = "\n".join(f"line{n}" for n in range(10))
        frame = compose_frame(body, "FOOTER", height=4)
        rows = frame.split("\n")

        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1], "FOOTER")
        self.assertEqual(rows[:3], ["line0", "line1", "line2"])


class ResolveKeyTuneEntryTest(unittest.TestCase):
    def test_t_enters_tune(self) -> None:
        self.assertEqual(resolve_key("t", VIEW_POWER), "tune")
        self.assertEqual(resolve_key("T", VIEW_WEATHER), "tune")


class ResolveTuneKeyTest(unittest.TestCase):
    def test_commit_and_cancel(self) -> None:
        self.assertEqual(resolve_tune_key("\r"), "commit")
        self.assertEqual(resolve_tune_key("\n"), "commit")
        self.assertEqual(resolve_tune_key("\x1b"), "cancel")  # Esc
        self.assertEqual(resolve_tune_key("q"), "cancel")
        self.assertEqual(resolve_tune_key("t"), "cancel")

    def test_adjust_select_and_step(self) -> None:
        self.assertEqual(resolve_tune_key("+"), "up")
        self.assertEqual(resolve_tune_key("="), "up")
        self.assertEqual(resolve_tune_key("-"), "down")
        self.assertEqual(resolve_tune_key("0"), "select0")
        self.assertEqual(resolve_tune_key("1"), "select1")
        self.assertEqual(resolve_tune_key("\t"), "toggle")
        self.assertEqual(resolve_tune_key("["), "step_down")
        self.assertEqual(resolve_tune_key("]"), "step_up")

    def test_unknown_ignored(self) -> None:
        self.assertIsNone(resolve_tune_key("x"))
        self.assertIsNone(resolve_tune_key(" "))


class ScalarsFromSnapshotTest(unittest.TestCase):
    def test_maps_controllers_to_scalar_setpoints(self) -> None:
        payload = {
            "solar": [
                {"id": "classic.0", "settings": {"absorb_voltage_v": 55.2}},
                {"id": "epever.1", "settings": {"absorb_voltage_v": 54.7}},
            ]
        }
        self.assertEqual(scalars_from_snapshot(payload), {0: 55.2, 1: 54.7})

    def test_skips_controllers_without_settings(self) -> None:
        payload = {"solar": [{"id": "classic.0"}, {"id": "epever.1", "settings": {}}]}
        self.assertEqual(scalars_from_snapshot(payload), {})


class TuneStateTest(unittest.TestCase):
    def test_adjust_stages_without_touching_base(self) -> None:
        tune = TuneState({0: 55.2, 1: 54.7})
        self.assertEqual(tune.controller, 0)
        tune.adjust(+1)  # default step 0.1
        self.assertEqual(tune.pending[0], 55.3)
        self.assertEqual(tune.bases[0], 55.2)  # base unchanged until commit
        self.assertEqual(tune.net_delta(0), 0.1)
        self.assertTrue(tune.dirty())

    def test_select_and_toggle(self) -> None:
        tune = TuneState({0: 55.2, 1: 54.7})
        tune.select(1)
        self.assertEqual(tune.controller, 1)
        tune.toggle()
        self.assertEqual(tune.controller, 0)

    def test_session_budget_caps_staging(self) -> None:
        tune = TuneState({0: 55.0})
        # step 0.1; budget 0.5 -> 5 steps allowed, the 6th is refused
        for _ in range(5):
            tune.adjust(+1)
        self.assertAlmostEqual(tune.net_delta(0), TUNE_SESSION_BUDGET_V, places=2)
        tune.adjust(+1)
        self.assertAlmostEqual(tune.net_delta(0), TUNE_SESSION_BUDGET_V, places=2)  # unchanged
        self.assertIn("budget", tune.message)

    def test_step_cycle_clamps(self) -> None:
        tune = TuneState({0: 55.0})
        tune.cycle_step(-1)
        self.assertEqual(tune.step, 0.05)
        tune.cycle_step(-1)
        self.assertEqual(tune.step, 0.05)  # clamped at smallest
        tune.cycle_step(+1)
        tune.cycle_step(+1)
        tune.cycle_step(+1)
        self.assertEqual(tune.step, 0.2)  # clamped at largest


class CommitTuneTest(unittest.TestCase):
    def test_commit_posts_net_delta_and_advances_base(self) -> None:
        tune = TuneState({0: 55.2, 1: 54.7})
        tune.adjust(+1)
        tune.adjust(+1)  # stage +0.2 on Classic
        calls = []

        def fake_post(control_url, controller, delta_v, timeout=5.0):
            calls.append((controller, delta_v))
            return {"ok": True, "voltage_v": round(55.2 + delta_v, 2), "confirmed": True}

        import offgrid_power.cli.api_terminal_display as mod

        original = mod.post_nudge
        mod.post_nudge = fake_post
        try:
            ok = commit_tune(tune, "http://x")
        finally:
            mod.post_nudge = original

        self.assertTrue(ok)
        self.assertEqual(calls, [(0, 0.2)])  # only the dirty controller, one delta
        self.assertEqual(tune.bases[0], 55.4)  # base advanced to achieved voltage
        self.assertFalse(tune.dirty())

    def test_commit_reports_refusal_and_discards_stage(self) -> None:
        tune = TuneState({0: 55.2})
        tune.adjust(+1)

        import offgrid_power.cli.api_terminal_display as mod

        original = mod.post_nudge
        mod.post_nudge = lambda *a, **k: {"ok": False, "error": "exceeds BMS CVL"}
        try:
            ok = commit_tune(tune, "http://x")
        finally:
            mod.post_nudge = original

        self.assertFalse(ok)
        self.assertIn("refused", tune.message)
        self.assertEqual(tune.pending[0], tune.bases[0])  # stage discarded


class TuneFooterTest(unittest.TestCase):
    def test_marks_selection_and_staged_change(self) -> None:
        tune = TuneState({0: 55.2, 1: 54.7})
        tune.adjust(+1)  # stage +0.1 on Classic (selected)
        panel = tune_footer(tune)
        self.assertIn("TUNE charge voltage", panel)
        self.assertIn("> [0] Classic", panel)
        self.assertIn("→ 55.30V (+0.10)", panel)
        self.assertIn("[Enter] apply", panel)


class ComposeFrameMultilineFooterTest(unittest.TestCase):
    def test_pins_multiline_footer_block_to_bottom(self) -> None:
        frame = compose_frame("body", "f1\nf2\nf3", height=6)
        rows = frame.split("\n")
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-3:], ["f1", "f2", "f3"])
        self.assertEqual(rows[0], "body")
        self.assertFalse(frame.endswith("\n"))


if __name__ == "__main__":
    unittest.main()

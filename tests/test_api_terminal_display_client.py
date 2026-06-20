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
    SCALING_SESSION_LIMIT,
    VIEW_POWER,
    VIEW_WEATHER,
    VOLTAGE_SESSION_BUDGET_V,
    TuneState,
    _with_refresh,
    scaling_factor_from_snapshot,
    build_tunables,
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
        self.assertEqual(resolve_tune_key("0"), "select:v0")
        self.assertEqual(resolve_tune_key("1"), "select:v1")
        self.assertEqual(resolve_tune_key("s"), "select:scaling")
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


class BudgetFractionFromSnapshotTest(unittest.TestCase):
    def test_reads_fraction_from_allocation(self) -> None:
        self.assertEqual(
            scaling_factor_from_snapshot({"allocation": {"ccl_scaling_factor": 0.5}}), 0.5
        )

    def test_none_when_absent_or_no_allocation(self) -> None:
        self.assertIsNone(scaling_factor_from_snapshot({"allocation": None}))
        self.assertIsNone(scaling_factor_from_snapshot({"allocation": {}}))
        self.assertIsNone(scaling_factor_from_snapshot({}))


class BuildTunablesTest(unittest.TestCase):
    def test_builds_voltage_rows_then_budget(self) -> None:
        rows = build_tunables({0: 55.2, 1: 54.7}, 0.5)
        self.assertEqual([r.key for r in rows], ["v0", "v1", "scaling"])
        self.assertEqual(rows[0].kind, "voltage")
        self.assertEqual(rows[0].controller, 0)
        self.assertEqual(rows[2].kind, "scaling")
        self.assertEqual(rows[2].base, 0.5)

    def test_omits_budget_when_unavailable(self) -> None:
        rows = build_tunables({0: 55.2}, None)
        self.assertEqual([r.key for r in rows], ["v0"])


class TuneStateTest(unittest.TestCase):
    def _state(self, budget=0.5) -> TuneState:
        return TuneState(build_tunables({0: 55.2, 1: 54.7}, budget))

    def test_adjust_stages_without_touching_base(self) -> None:
        tune = self._state()
        self.assertEqual(tune.current.key, "v0")
        tune.adjust(+1)  # default voltage step 0.1
        self.assertEqual(tune.current.pending, 55.3)
        self.assertEqual(tune.current.base, 55.2)  # base unchanged until commit
        self.assertEqual(tune.current.net, 0.1)
        self.assertTrue(tune.dirty())

    def test_select_by_key_and_toggle(self) -> None:
        tune = self._state()
        tune.select("scaling")
        self.assertEqual(tune.current.key, "scaling")
        tune.toggle()
        self.assertEqual(tune.current.key, "v0")  # wraps back to the top

    def test_scaling_row_steps_in_fraction(self) -> None:
        tune = self._state()
        tune.select("scaling")
        tune.adjust(+1)  # default scaling step 0.1 (10 points)
        self.assertEqual(tune.current.pending, 0.6)
        self.assertEqual(tune.current.fmt_net(), "+10")

    def test_session_budget_caps_staging(self) -> None:
        tune = TuneState(build_tunables({0: 55.0}, None))
        # voltage step 0.1; budget 0.5 -> 5 steps allowed, the 6th is refused
        for _ in range(5):
            tune.adjust(+1)
        self.assertAlmostEqual(tune.current.net, VOLTAGE_SESSION_BUDGET_V, places=2)
        tune.adjust(+1)
        self.assertAlmostEqual(tune.current.net, VOLTAGE_SESSION_BUDGET_V, places=2)  # unchanged
        self.assertIn("budget", tune.message)

    def test_scaling_session_cap(self) -> None:
        tune = TuneState(build_tunables({}, 0.5))
        tune.cycle_step(-1)  # drop to 0.05 so we can land exactly on the 0.25 cap
        for _ in range(5):
            tune.adjust(+1)
        self.assertAlmostEqual(tune.current.net, SCALING_SESSION_LIMIT, places=4)
        tune.adjust(+1)
        self.assertAlmostEqual(tune.current.net, SCALING_SESSION_LIMIT, places=4)  # unchanged
        self.assertIn("budget", tune.message)

    def test_step_cycle_clamps_per_row(self) -> None:
        tune = self._state()  # selected row is v0 (voltage)
        tune.cycle_step(-1)
        self.assertEqual(tune.current.step, 0.05)
        tune.cycle_step(-1)
        self.assertEqual(tune.current.step, 0.05)  # clamped at smallest
        for _ in range(3):
            tune.cycle_step(+1)
        self.assertEqual(tune.current.step, 0.2)  # clamped at largest


class CommitTuneTest(unittest.TestCase):
    def test_commit_posts_net_delta_and_advances_base(self) -> None:
        tune = TuneState(build_tunables({0: 55.2, 1: 54.7}, 0.5))
        tune.adjust(+1)
        tune.adjust(+1)  # stage +0.2 on Classic (v0)
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
        self.assertEqual(calls, [(0, 0.2)])  # only the dirty row, one delta
        self.assertEqual(tune.tunables[0].base, 55.4)  # base advanced to achieved voltage
        self.assertFalse(tune.dirty())

    def test_commit_posts_scaling_delta(self) -> None:
        tune = TuneState(build_tunables({}, 0.5))
        tune.adjust(+1)  # +0.1 on the scaling row (default step)
        calls = []

        def fake_budget(control_url, delta, timeout=5.0):
            calls.append(delta)
            return {"ok": True, "factor": round(0.5 + delta, 4)}

        import offgrid_power.cli.api_terminal_display as mod

        original = mod.post_scaling_nudge
        mod.post_scaling_nudge = fake_budget
        try:
            ok = commit_tune(tune, "http://x")
        finally:
            mod.post_scaling_nudge = original

        self.assertTrue(ok)
        self.assertEqual(calls, [0.1])
        self.assertEqual(tune.tunables[0].base, 0.6)

    def test_commit_reports_refusal_and_discards_stage(self) -> None:
        tune = TuneState(build_tunables({0: 55.2}, None))
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
        self.assertEqual(tune.tunables[0].pending, tune.tunables[0].base)  # stage discarded


class TuneFooterTest(unittest.TestCase):
    def test_marks_selection_and_staged_change(self) -> None:
        tune = TuneState(build_tunables({0: 55.2, 1: 54.7}, 0.5))
        tune.adjust(+1)  # stage +0.1 on Classic (v0, selected)
        panel = tune_footer(tune)
        self.assertIn("TUNE charge", panel)
        self.assertIn("> [0] Classic", panel)
        self.assertIn("→ 55.30V (+0.10)", panel)
        self.assertIn("[s] CCL scaling", panel)
        self.assertIn("50%", panel)
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

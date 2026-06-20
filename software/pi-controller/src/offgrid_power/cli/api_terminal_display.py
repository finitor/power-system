"""Terminal display client for the supervisor HTTP API.

On an interactive tty the view is switchable with single keypresses:
``p`` power, ``w`` weather, space toggles, ``q`` quits. A keypress wakes
the refresh loop immediately rather than waiting out the interval. When
stdin is not a tty (piped output, ``--once``) the loop just refreshes on
the interval and the keys are inert.

``t`` opens *tune mode*, where the operator stages small scalar charge-voltage
nudges for either controller and applies them with one Enter (staged-commit, so
a stray keypress can't move the system — the change is only sent on the explicit
commit). Nudges go through the supervisor's delta API. Tune mode is bounded by a
per-session budget and auto-exits after a spell of inactivity; the supervisor's
BMS-CVL guard is the hard backstop regardless of what this client sends.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import json
import os
import select
import shutil
import signal
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from offgrid_power.api_terminal_display import (
    render_api_snapshot,
    render_api_unavailable,
    render_api_weather,
)
from offgrid_power.terminal_display import clear_screen, highlight_changed_digits

VIEW_POWER = "power"
VIEW_WEATHER = "weather"

SNAPSHOT_SUFFIX = "/api/v1/snapshot"
WEATHER_SUFFIX = "/api/v1/weather"
VOLTAGE_CONTROL_SUFFIX = "/api/v1/control/charge-controller/voltage"
CCL_BUDGET_CONTROL_SUFFIX = "/api/v1/control/charge-budget/ccl-fraction"

# Tune mode disarms after this long without a keypress so it's never left live.
TUNE_IDLE_TIMEOUT_S = 20.0

# Per-tunable knobs. Steps are what the operator cycles with [ / ]; the session
# budget caps how far one tune session can wander from where it opened (re-arm
# to go further). Controller voltages are in volts; the CCL budget is a fraction
# (0-1) shown as a percent and stepped 5 points at a time.
VOLTAGE_STEPS_V = (0.05, 0.1, 0.2)
VOLTAGE_DEFAULT_STEP_INDEX = 1
VOLTAGE_SESSION_BUDGET_V = 0.5

BUDGET_STEPS = (0.01, 0.05, 0.1)
BUDGET_DEFAULT_STEP_INDEX = 1
BUDGET_SESSION_BUDGET = 0.25

CONTROLLER_LABELS = {0: "Classic", 1: "EPEver"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the supervisor API snapshot in a terminal.")
    parser.add_argument("--url", default="http://127.0.0.1:8080/api/v1/snapshot")
    parser.add_argument(
        "--weather-url",
        default=None,
        help="Weather API URL (defaults to the snapshot URL with the weather path)",
    )
    parser.add_argument(
        "--control-url",
        default="http://127.0.0.1:8081",
        help="Supervisor control base URL for tune-mode voltage nudges "
        "(local-only by default, not the nginx display port)",
    )
    parser.add_argument("--view", choices=[VIEW_POWER, VIEW_WEATHER], default=VIEW_POWER)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--no-clear", action="store_true", help="Do not clear the terminal before each redraw")
    parser.add_argument("--once", action="store_true", help="Render one snapshot and exit")
    return parser.parse_args()


def derive_weather_url(snapshot_url: str) -> str:
    if snapshot_url.endswith(SNAPSHOT_SUFFIX):
        return snapshot_url[: -len(SNAPSHOT_SUFFIX)] + WEATHER_SUFFIX
    return snapshot_url


def resolve_key(char: str, view: str) -> str | None:
    """Map a keypress to an action: a view name, "tune", "quit", or None."""
    lowered = char.lower()
    if lowered == "q":
        return "quit"
    if lowered == "p":
        return VIEW_POWER
    if lowered == "w":
        return VIEW_WEATHER
    if lowered == "t":
        return "tune"
    if char in (" ", "\t"):
        return VIEW_WEATHER if view == VIEW_POWER else VIEW_POWER
    return None


def resolve_tune_key(char: str) -> str | None:
    """Map a keypress in tune mode to an action, or None to ignore.

    Deliberately small and explicit so the key map is unit-testable without a
    tty. Esc and ``q`` cancel (leave tune mode without sending anything); only
    Enter commits. Arrow keys are intentionally not used — they arrive as
    multi-byte escape sequences that would tangle with the Esc-to-cancel read.
    """
    if char in ("\r", "\n"):
        return "commit"
    if char in ("\x1b", "q", "Q", "t", "T"):
        return "cancel"
    if char in ("+", "=", "k", "K"):
        return "up"
    if char in ("-", "_", "j", "J"):
        return "down"
    if char == "0":
        return "select:v0"
    if char == "1":
        return "select:v1"
    if char in ("b", "B"):
        return "select:budget"
    if char == "\t":
        return "toggle"
    if char == "[":
        return "step_down"
    if char == "]":
        return "step_up"
    return None


@dataclass
class Tunable:
    """One staged-edit row in the tune overlay (a controller voltage or the budget).

    ``base`` is the value at entry/last-commit; ``pending`` is the operator's
    staged target. Nothing leaves the client until commit. ``kind`` drives both
    the display units (volts vs percent) and which control endpoint a commit hits.
    """

    key: str
    label: str
    kind: str  # "voltage" | "budget"
    base: float
    pending: float
    steps: tuple[float, ...]
    step_index: int
    session_budget: float
    controller: int | None = None

    @property
    def step(self) -> float:
        return self.steps[self.step_index]

    @property
    def net(self) -> float:
        return round(self.pending - self.base, 4)

    @property
    def dirty(self) -> bool:
        return self.net != 0

    def fmt(self, value: float) -> str:
        return f"{value:.2f}V" if self.kind == "voltage" else f"{value * 100:.0f}%"

    def fmt_net(self) -> str:
        return f"{self.net:+.2f}" if self.kind == "voltage" else f"{self.net * 100:+.0f}"


def build_tunables(scalars: dict[int, float], budget_fraction: float | None) -> list[Tunable]:
    """Assemble the tune rows: each controller's scalar voltage, then the budget."""
    tunables: list[Tunable] = []
    for controller in sorted(scalars):
        tunables.append(
            Tunable(
                key=f"v{controller}",
                label=CONTROLLER_LABELS.get(controller, f"ctrl {controller}"),
                kind="voltage",
                base=round(scalars[controller], 2),
                pending=round(scalars[controller], 2),
                steps=VOLTAGE_STEPS_V,
                step_index=VOLTAGE_DEFAULT_STEP_INDEX,
                session_budget=VOLTAGE_SESSION_BUDGET_V,
                controller=controller,
            )
        )
    if budget_fraction is not None:
        tunables.append(
            Tunable(
                key="budget",
                label="CCL budget",
                kind="budget",
                base=round(budget_fraction, 4),
                pending=round(budget_fraction, 4),
                steps=BUDGET_STEPS,
                step_index=BUDGET_DEFAULT_STEP_INDEX,
                session_budget=BUDGET_SESSION_BUDGET,
            )
        )
    return tunables


class TuneState:
    """Staged edits for the tune overlay across a list of tunables.

    Holds a list of :class:`Tunable` rows and which one is selected. Nothing is
    sent to the supervisor until a commit; until then this is pure local
    bookkeeping bounded by each row's per-session budget.
    """

    def __init__(self, tunables: list[Tunable]):
        if not tunables:
            raise ValueError("tune mode needs at least one tunable")
        self.tunables = tunables
        self.index = 0
        self.message = ""

    @property
    def current(self) -> Tunable:
        return self.tunables[self.index]

    def dirty(self) -> bool:
        return any(t.dirty for t in self.tunables)

    def select(self, key: str) -> None:
        for i, tunable in enumerate(self.tunables):
            if tunable.key == key:
                self.index = i
                self.message = ""
                return

    def toggle(self) -> None:
        self.index = (self.index + 1) % len(self.tunables)
        self.message = ""

    def cycle_step(self, direction: int) -> None:
        tunable = self.current
        tunable.step_index = max(0, min(len(tunable.steps) - 1, tunable.step_index + direction))
        self.message = ""

    def adjust(self, direction: int) -> None:
        """Stage one step up (+1) or down (-1) on the selected tunable.

        Refused — with a message, not an exception — once the staged change
        would exceed that row's session budget from the entry value.
        """
        tunable = self.current
        candidate = round(tunable.pending + direction * tunable.step, 4)
        if abs(round(candidate - tunable.base, 4)) > tunable.session_budget + 1e-9:
            self.message = "session budget reached - apply or cancel"
            return
        tunable.pending = candidate
        self.message = ""


def footer(view: str) -> str:
    def tag(key: str, label: str, active: bool) -> str:
        marker = f"[{key}]"
        return f"{marker} {label.upper()}" if active else f"{marker} {label}"

    return "  ".join(
        [
            "",
            tag("p", "Power", view == VIEW_POWER),
            tag("w", "Weather", view == VIEW_WEATHER),
            "[space] Toggle",
            "[t] Tune",
            "[q] Quit",
            # Font size is an outer-tmux concern (F7/F8 at the root table), not
            # a renderer key — this is just a reminder the controls exist. The
            # 2 leading spaces add to the join's 2 for a 4-space gap that sets
            # this group apart from the view controls; 1 space within the group.
            "  Font ↓F7 ↑F8",
        ]
    )


def _tune_select_hint(tune: TuneState) -> str:
    keys = []
    for tunable in tune.tunables:
        if tunable.kind == "voltage" and tunable.controller is not None:
            keys.append(str(tunable.controller))
        elif tunable.key == "budget":
            keys.append("b")
    return "/".join(keys)


def tune_footer(tune: TuneState) -> str:
    """Render the multi-line tune-mode panel pinned at the bottom of the frame."""
    lines = ["", "── TUNE charge ──────────────────────"]
    for tunable in tune.tunables:
        selected = ">" if tunable is tune.current else " "
        tag = str(tunable.controller) if tunable.controller is not None else "b"
        staged = f"  → {tunable.fmt(tunable.pending)} ({tunable.fmt_net()})" if tunable.dirty else ""
        lines.append(f" {selected} [{tag}] {tunable.label:<10} {tunable.fmt(tunable.base)}{staged}")
    current = tune.current
    lines.append(f"   step {current.fmt(current.step).lstrip()}")
    lines.append(
        f"   [+/-] adjust  [{_tune_select_hint(tune)}] select  "
        "[ [ / ] ] step  [Enter] apply  [Esc] cancel"
    )
    if tune.message:
        lines.append(f"   {tune.message}")
    return "\n".join(lines)


def compose_frame(body: str, footer_line: str, height: int) -> str:
    """Pad the body so the footer sits on the bottom rows of a `height`-row pane.

    The body is rendered top-aligned and the footer (one line, or several for the
    tune panel) pinned to the last rows; the gap between them is blank. Returns a
    frame with no trailing newline so writing it leaves the cursor on the last
    row without scrolling.
    """
    lines = body.split("\n")
    footer_height = footer_line.count("\n") + 1
    visible = max(height - footer_height, 1)
    if len(lines) > visible:
        lines = lines[:visible]
    else:
        lines += [""] * (visible - len(lines))
    return "\n".join(lines) + "\n" + footer_line


@contextlib.contextmanager
def cbreak_mode(stream):
    """Put a tty stream in cbreak mode so single keypresses read without Enter."""
    if not stream.isatty():
        yield False
        return
    import termios
    import tty

    fd = stream.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


@contextlib.contextmanager
def resize_wakeup(enabled: bool):
    """Yield a read-fd that becomes readable on SIGWINCH (terminal resize).

    Lets the render loop re-pin the bottom footer the instant the console is
    resized — e.g. a font-size change reflows the tty taller — instead of
    leaving the footer dangling until the next periodic refresh. Uses a
    self-pipe wakeup fd so a plain select() wakes on the signal. Yields None
    when disabled (non-interactive) or unsupported. Must run on the main thread.
    """
    if not enabled or not hasattr(signal, "SIGWINCH"):
        yield None
        return
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    os.set_blocking(write_fd, False)
    previous_wakeup = signal.set_wakeup_fd(write_fd)
    # A handler must be installed for Python to route the signal to the wakeup
    # fd; the body itself does nothing — the readable fd is the signal.
    previous_handler = signal.signal(signal.SIGWINCH, lambda *_: None)
    try:
        yield read_fd
    finally:
        signal.signal(signal.SIGWINCH, previous_handler)
        signal.set_wakeup_fd(previous_wakeup)
        os.close(read_fd)
        os.close(write_fd)


def main() -> int:
    args = parse_args()
    weather_url = args.weather_url or derive_weather_url(args.url)
    view = args.view
    previous_render: str | None = None
    tune: TuneState | None = None
    last_activity = time.monotonic()

    # A manual panel load queues an out-of-cycle source poll (fire-and-forget,
    # server-side), so the next refresh shows fresh data without this fetch
    # ever blocking on a slow device. True on first paint and after each switch.
    pending_refresh = True

    with cbreak_mode(sys.stdin) as raw, resize_wakeup(raw and not args.once) as winch_fd:
        interactive = raw and not args.once
        try:
            while True:
                # Tune mode auto-disarms after a spell of inactivity so the
                # system is never left armed for edits unattended.
                if tune is not None and time.monotonic() - last_activity > TUNE_IDLE_TIMEOUT_S:
                    tune = None
                    previous_render = None
                started = time.monotonic()
                # While tuning, keep the power panel up so the operator watches
                # the taper react; the tune overlay replaces the footer.
                display_view = VIEW_POWER if tune is not None else view
                rendered = render_view(display_view, args.url, weather_url, timeout=args.timeout, refresh=pending_refresh)
                pending_refresh = False
                body = highlight_changed_digits(previous_render, rendered)
                footer_text = tune_footer(tune) if tune is not None else footer(view)
                if not args.no_clear:
                    clear_screen()
                if interactive:
                    height = shutil.get_terminal_size((80, 24)).lines
                    sys.stdout.write(compose_frame(body, footer_text, height))
                    sys.stdout.flush()
                else:
                    print(body, flush=True)
                previous_render = rendered
                if args.once:
                    return 0

                # Wait out the refresh interval, but cut it short on a recognized
                # keypress (switch view, stage/apply a nudge) or a terminal resize.
                while True:
                    remaining = args.interval - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    if not interactive:
                        time.sleep(remaining)
                        break
                    watch = [sys.stdin] if winch_fd is None else [sys.stdin, winch_fd]
                    ready = select.select(watch, [], [], remaining)[0]
                    if not ready:
                        continue
                    if winch_fd is not None and winch_fd in ready:
                        # Console resized (e.g. font-size change reflowed the
                        # tty): drain the wakeup byte(s) and re-render now so the
                        # bottom-pinned panel follows the new viewport height.
                        try:
                            os.read(winch_fd, 4096)
                        except OSError:
                            pass
                        previous_render = None  # geometry changed: no digit diff
                        break
                    char = sys.stdin.read(1)
                    if not char:
                        continue
                    last_activity = time.monotonic()
                    if tune is not None:
                        outcome = _handle_tune_key(char, tune, args)
                        if outcome is None:
                            continue  # unrecognized key: keep waiting, no redraw
                        if outcome == "exit":
                            tune = None
                        elif outcome == "applied":
                            pending_refresh = True  # re-poll so settings/taper reflect the write
                        previous_render = None
                        break
                    action = resolve_key(char, view)
                    if action == "quit":
                        return 0
                    if action is None:
                        continue
                    if action == "tune":
                        tune = _enter_tune(args)
                        if tune is not None:
                            view = VIEW_POWER
                        previous_render = None
                        break
                    if action != view:
                        view = action
                        previous_render = None  # different view: no digit diff to carry
                    pending_refresh = True  # any panel keypress re-polls sources
                    break
        except KeyboardInterrupt:
            print()
            return 0


def _enter_tune(args: argparse.Namespace) -> TuneState | None:
    """Fetch current setpoints and open tune mode, or return None if unavailable."""
    try:
        scalars, budget_fraction = fetch_tune_inputs(args.url, timeout=args.timeout)
    except (OSError, URLError, json.JSONDecodeError):
        return None
    tunables = build_tunables(scalars, budget_fraction)
    if not tunables:
        return None
    return TuneState(tunables)


def _handle_tune_key(char: str, tune: TuneState, args: argparse.Namespace) -> str | None:
    """Apply one tune-mode keypress.

    Returns ``None`` if the key was ignored, ``"exit"`` to leave tune mode
    (cancel — staged edits discarded), ``"applied"`` after a commit, or
    ``"staged"`` for a local-only adjustment.
    """
    action = resolve_tune_key(char)
    if action is None:
        return None
    if action == "cancel":
        return "exit"
    if action == "commit":
        commit_tune(tune, args.control_url, timeout=args.timeout)
        return "applied"
    if action == "up":
        tune.adjust(+1)
    elif action == "down":
        tune.adjust(-1)
    elif action.startswith("select:"):
        tune.select(action.split(":", 1)[1])
    elif action == "toggle":
        tune.toggle()
    elif action == "step_down":
        tune.cycle_step(-1)
    elif action == "step_up":
        tune.cycle_step(+1)
    return "staged"


def render_view(view: str, url: str, weather_url: str, timeout: float = 5.0, refresh: bool = False) -> str:
    if view == VIEW_WEATHER:
        # A panel switch queues a background forecast re-fetch (non-blocking);
        # the fresh data lands on the next refresh.
        return render_weather_once(weather_url, timeout=timeout, refresh=refresh)
    return render_once(url, timeout=timeout, refresh=refresh)


def _with_refresh(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}refresh=1"


def render_once(url: str, timeout: float = 5.0, refresh: bool = False) -> str:
    if refresh:
        url = _with_refresh(url)
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return render_api_snapshot(payload)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return render_api_unavailable(f"HTTP {exc.code}: {detail or exc.reason}")
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return render_api_unavailable(str(exc))


def render_weather_once(url: str, timeout: float = 5.0, refresh: bool = False) -> str:
    if refresh:
        url = _with_refresh(url)
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return render_api_weather(payload)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return render_api_unavailable(f"HTTP {exc.code}: {detail or exc.reason}")
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return render_api_unavailable(str(exc))


def scalars_from_snapshot(payload: dict) -> dict[int, float]:
    """Pull each controller's current scalar setpoint out of a snapshot payload.

    The scalar is the absorb-equivalent setpoint the supervisor exposes for both
    controllers as ``settings.absorb_voltage_v`` (the EPEver aliases boost there).
    Controllers without a readable setpoint are skipped.
    """
    scalars: dict[int, float] = {}
    for entry in payload.get("solar") or []:
        controller = _controller_index(entry.get("id"))
        if controller is None:
            continue
        settings = entry.get("settings") or {}
        value = settings.get("absorb_voltage_v")
        if isinstance(value, (int, float)):
            scalars[controller] = round(float(value), 2)
    return scalars


def _controller_index(controller_id) -> int | None:
    if not isinstance(controller_id, str):
        return None
    head = controller_id.split(".", 1)[0]
    if head == "classic":
        return 0
    if head == "epever":
        return 1
    return None


def budget_fraction_from_snapshot(payload: dict) -> float | None:
    """Pull the live CCL budget fraction out of a snapshot's allocation block.

    None when allocation isn't running (no allocation block) or the field is
    absent — in which case tune mode simply omits the budget row.
    """
    value = (payload.get("allocation") or {}).get("ccl_budget_fraction")
    return round(float(value), 4) if isinstance(value, (int, float)) else None


def fetch_tune_inputs(url: str, timeout: float = 5.0) -> tuple[dict[int, float], float | None]:
    """GET the snapshot and return (scalar voltages, CCL budget fraction)."""
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return scalars_from_snapshot(payload), budget_fraction_from_snapshot(payload)


def _post_control(control_url: str, suffix: str, body: dict, timeout: float) -> dict:
    """POST a control request and return its JSON reply.

    Raises on transport failure; an HTTP error response is parsed and returned
    so the caller can surface the supervisor's reason (e.g. a refused write).
    """
    url = control_url.rstrip("/") + suffix
    data = json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(detail)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"HTTP {exc.code}: {detail.strip() or exc.reason}"}


def post_nudge(control_url: str, controller: int, delta_v: float, timeout: float = 5.0) -> dict:
    """POST a scalar-voltage delta to the supervisor and return its JSON reply."""
    return _post_control(
        control_url, VOLTAGE_CONTROL_SUFFIX, {"controller": controller, "delta_v": round(delta_v, 2)}, timeout
    )


def post_budget_nudge(control_url: str, delta: float, timeout: float = 5.0) -> dict:
    """POST a CCL budget-fraction delta to the supervisor and return its reply."""
    return _post_control(control_url, CCL_BUDGET_CONTROL_SUFFIX, {"delta": round(delta, 4)}, timeout)


def _commit_one(tunable: Tunable, control_url: str, timeout: float) -> str:
    """Send one tunable's staged net change; update its base; return a status word.

    On success the base advances to the achieved value (so the operator can keep
    nudging from there). On refusal/failure the stage is discarded back to base.
    """
    delta = tunable.net
    try:
        if tunable.kind == "budget":
            reply = post_budget_nudge(control_url, delta, timeout=timeout)
        else:
            reply = post_nudge(control_url, tunable.controller, delta, timeout=timeout)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        tunable.pending = tunable.base
        return f"{tunable.label} failed: {exc}"

    if not reply.get("ok"):
        tunable.pending = tunable.base  # discard the rejected stage
        return f"{tunable.label} refused: {reply.get('error', 'unknown error')}"

    if tunable.kind == "budget":
        achieved = round(float(reply.get("fraction", tunable.pending)), 4)
        tunable.base = tunable.pending = achieved
        return f"{tunable.label} {tunable.fmt(achieved)}"
    achieved = round(float(reply.get("voltage_v", tunable.pending)), 2)
    tunable.base = tunable.pending = achieved
    mark = "ok" if reply.get("confirmed") else "UNCONFIRMED"
    return f"{tunable.label} {tunable.fmt(achieved)} {mark}"


def commit_tune(tune: TuneState, control_url: str, timeout: float = 5.0) -> bool:
    """Apply every staged change as one delta each; return whether all succeeded.

    Results are reported through ``tune.message``.
    """
    dirty = [t for t in tune.tunables if t.dirty]
    if not dirty:
        tune.message = "nothing staged"
        return False
    outcomes = [_commit_one(t, control_url, timeout) for t in dirty]
    tune.message = "  |  ".join(outcomes)
    return all("refused" not in o and "failed" not in o for o in outcomes)


if __name__ == "__main__":
    raise SystemExit(main())

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

# Tune-mode tuning. Steps the operator can cycle through; the per-session budget
# caps how far one tune session can wander from where it started (re-arm to go
# further); the idle timeout disarms tune mode so it's never left live.
TUNE_STEP_SIZES_V = (0.05, 0.1, 0.2)
TUNE_DEFAULT_STEP_INDEX = 1
TUNE_SESSION_BUDGET_V = 0.5
TUNE_IDLE_TIMEOUT_S = 20.0

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
        return "select0"
    if char == "1":
        return "select1"
    if char == "\t":
        return "toggle"
    if char == "[":
        return "step_down"
    if char == "]":
        return "step_up"
    return None


class TuneState:
    """Staged scalar-voltage edits for the tune-mode overlay.

    Holds the setpoint each controller had at entry/last-commit (``bases``) and
    the operator's staged target (``pending``). Nothing is sent to the
    supervisor until a commit; until then this is pure local bookkeeping bounded
    by the per-session budget.
    """

    def __init__(self, scalars: dict[int, float]):
        if not scalars:
            raise ValueError("tune mode needs at least one controller scalar voltage")
        self.bases: dict[int, float] = {c: round(v, 2) for c, v in scalars.items()}
        self.pending: dict[int, float] = dict(self.bases)
        self.controller: int = min(self.bases)
        self.step_index: int = TUNE_DEFAULT_STEP_INDEX
        self.message: str = ""

    @property
    def step(self) -> float:
        return TUNE_STEP_SIZES_V[self.step_index]

    def net_delta(self, controller: int) -> float:
        return round(self.pending[controller] - self.bases[controller], 2)

    def dirty(self) -> bool:
        return any(self.net_delta(c) != 0 for c in self.bases)

    def select(self, controller: int) -> None:
        if controller in self.bases:
            self.controller = controller
            self.message = ""

    def toggle(self) -> None:
        controllers = sorted(self.bases)
        index = controllers.index(self.controller)
        self.controller = controllers[(index + 1) % len(controllers)]
        self.message = ""

    def cycle_step(self, direction: int) -> None:
        self.step_index = max(0, min(len(TUNE_STEP_SIZES_V) - 1, self.step_index + direction))
        self.message = ""

    def adjust(self, direction: int) -> None:
        """Stage one step up (+1) or down (-1) on the selected controller.

        Refused — with a message, not an exception — once the staged change
        would exceed the session budget from the entry value.
        """
        controller = self.controller
        candidate = round(self.pending[controller] + direction * self.step, 2)
        if abs(round(candidate - self.bases[controller], 2)) > TUNE_SESSION_BUDGET_V + 1e-9:
            self.message = f"session budget +/-{TUNE_SESSION_BUDGET_V:.2f}V reached - apply or cancel"
            return
        self.pending[controller] = candidate
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


def tune_footer(tune: TuneState) -> str:
    """Render the multi-line tune-mode panel pinned at the bottom of the frame."""
    lines = ["", "── TUNE charge voltage ──────────────"]
    for controller in sorted(tune.bases):
        selected = ">" if controller == tune.controller else " "
        label = CONTROLLER_LABELS.get(controller, f"ctrl {controller}")
        base = tune.bases[controller]
        net = tune.net_delta(controller)
        staged = f"  → {tune.pending[controller]:.2f}V ({net:+.2f})" if net else ""
        lines.append(f" {selected} [{controller}] {label:<7} {base:.2f}V{staged}")
    lines.append(f"   step {tune.step:.2f}V   budget ±{TUNE_SESSION_BUDGET_V:.2f}V")
    lines.append("   [+/-] adjust  [0/1] select  [ [ / ] ] step  [Enter] apply  [Esc] cancel")
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
    """Fetch current scalars and open tune mode, or return None if unavailable."""
    try:
        scalars = fetch_scalars(args.url, timeout=args.timeout)
    except (OSError, URLError, json.JSONDecodeError):
        return None
    if not scalars:
        return None
    return TuneState(scalars)


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
    elif action == "select0":
        tune.select(0)
    elif action == "select1":
        tune.select(1)
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


def fetch_scalars(url: str, timeout: float = 5.0) -> dict[int, float]:
    """GET the snapshot and return current scalar voltages, or raise on failure."""
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return scalars_from_snapshot(payload)


def post_nudge(control_url: str, controller: int, delta_v: float, timeout: float = 5.0) -> dict:
    """POST a scalar-voltage delta to the supervisor and return its JSON reply.

    Raises on transport failure; an HTTP error response is parsed and returned
    so the caller can surface the supervisor's reason (e.g. a refused write).
    """
    url = control_url.rstrip("/") + VOLTAGE_CONTROL_SUFFIX
    body = json.dumps({"controller": controller, "delta_v": round(delta_v, 2)}).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(detail)
        except json.JSONDecodeError:
            return {"ok": False, "error": f"HTTP {exc.code}: {detail.strip() or exc.reason}"}


def commit_tune(tune: TuneState, control_url: str, timeout: float = 5.0) -> bool:
    """Apply every staged controller change as one delta each; return success.

    Updates the tune state in place: on a confirmed write the controller's base
    advances to the achieved voltage (so the operator can keep nudging from
    there); the result is reported through ``tune.message``.
    """
    pending = [c for c in sorted(tune.bases) if tune.net_delta(c) != 0]
    if not pending:
        tune.message = "nothing staged"
        return False
    outcomes: list[str] = []
    all_ok = True
    for controller in pending:
        delta = tune.net_delta(controller)
        label = CONTROLLER_LABELS.get(controller, f"ctrl {controller}")
        try:
            reply = post_nudge(control_url, controller, delta, timeout=timeout)
        except (OSError, URLError, json.JSONDecodeError) as exc:
            all_ok = False
            outcomes.append(f"{label} failed: {exc}")
            continue
        if reply.get("ok"):
            achieved = reply.get("voltage_v", tune.pending[controller])
            tune.bases[controller] = round(float(achieved), 2)
            tune.pending[controller] = tune.bases[controller]
            mark = "ok" if reply.get("confirmed") else "UNCONFIRMED"
            outcomes.append(f"{label} {achieved:.2f}V {mark}")
        else:
            all_ok = False
            tune.pending[controller] = tune.bases[controller]  # discard the rejected stage
            outcomes.append(f"{label} refused: {reply.get('error', 'unknown error')}")
    tune.message = "  |  ".join(outcomes)
    return all_ok


if __name__ == "__main__":
    raise SystemExit(main())

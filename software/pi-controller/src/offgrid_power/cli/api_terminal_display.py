"""Terminal display client for the supervisor HTTP API.

On an interactive tty the view is switchable with single keypresses:
``p`` power, ``w`` weather, space toggles, ``q`` quits. A keypress wakes
the refresh loop immediately rather than waiting out the interval. When
stdin is not a tty (piped output, ``--once``) the loop just refreshes on
the interval and the keys are inert.
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
from urllib.request import urlopen

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the supervisor API snapshot in a terminal.")
    parser.add_argument("--url", default="http://127.0.0.1:8080/api/v1/snapshot")
    parser.add_argument(
        "--weather-url",
        default=None,
        help="Weather API URL (defaults to the snapshot URL with the weather path)",
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
    """Map a keypress to an action: a view name, "quit", or None to ignore."""
    lowered = char.lower()
    if lowered == "q":
        return "quit"
    if lowered == "p":
        return VIEW_POWER
    if lowered == "w":
        return VIEW_WEATHER
    if char in (" ", "\t"):
        return VIEW_WEATHER if view == VIEW_POWER else VIEW_POWER
    return None


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
            "[q] Quit",
            # Font size is an outer-tmux concern (F7/F8 at the root table), not
            # a renderer key — this is just a reminder the controls exist. The
            # 2 leading spaces add to the join's 2 for a 4-space gap that sets
            # this group apart from the view controls; 1 space within the group.
            "  Font ↓F7 ↑F8",
        ]
    )


def compose_frame(body: str, footer_line: str, height: int) -> str:
    """Pad the body so the footer sits on the bottom row of a `height`-row pane.

    The body is rendered top-aligned and the footer pinned to the last row;
    the gap between them is blank. Returns a frame with no trailing newline so
    writing it leaves the cursor on the footer row without scrolling.
    """
    lines = body.split("\n")
    visible = max(height - 1, 1)
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

    # A manual panel load queues an out-of-cycle source poll (fire-and-forget,
    # server-side), so the next refresh shows fresh data without this fetch
    # ever blocking on a slow device. True on first paint and after each switch.
    pending_refresh = True

    with cbreak_mode(sys.stdin) as raw, resize_wakeup(raw and not args.once) as winch_fd:
        interactive = raw and not args.once
        try:
            while True:
                started = time.monotonic()
                rendered = render_view(view, args.url, weather_url, timeout=args.timeout, refresh=pending_refresh)
                pending_refresh = False
                body = highlight_changed_digits(previous_render, rendered)
                if not args.no_clear:
                    clear_screen()
                if interactive:
                    height = shutil.get_terminal_size((80, 24)).lines
                    sys.stdout.write(compose_frame(body, footer(view), height))
                    sys.stdout.flush()
                else:
                    print(body, flush=True)
                previous_render = rendered
                if args.once:
                    return 0

                # Wait out the refresh interval, but cut it short on a recognized
                # keypress (switch view) or a terminal resize (re-pin the footer).
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
                        # bottom-pinned footer follows the new viewport height.
                        try:
                            os.read(winch_fd, 4096)
                        except OSError:
                            pass
                        previous_render = None  # geometry changed: no digit diff
                        break
                    char = sys.stdin.read(1)
                    if not char:
                        continue
                    action = resolve_key(char, view)
                    if action == "quit":
                        return 0
                    if action is None:
                        continue
                    if action != view:
                        view = action
                        previous_render = None  # different view: no digit diff to carry
                    pending_refresh = True  # any panel keypress re-polls sources
                    break
        except KeyboardInterrupt:
            print()
            return 0


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


if __name__ == "__main__":
    raise SystemExit(main())

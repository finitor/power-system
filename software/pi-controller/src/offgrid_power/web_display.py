"""Primitive HTML rendering and serving for supervisor metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from threading import Lock
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .api_terminal_display import render_api_snapshot, render_api_weather
from .charge_stage import NormalizedStage
from .load import LoadSampleBuffer, LoadSummary, LoadTracker
from .supervisor import STATUS_ERROR, Supervisor, SupervisorSnapshot, snapshot_severity_text, snapshot_status_annotations
from .terminal_display import format_cell_location_for_display, format_time, format_updated_time
from .weather import WeatherReport, weather_api_payload


KINDLE_REFRESH_SECONDS = 60
WEATHER_STALE_AFTER = timedelta(hours=1)
BATTERY_IDLE_CURRENT_A = 0.5
BROWSER_POWER_REFRESH_SECONDS = 30
BROWSER_WEATHER_REFRESH_SECONDS = 300
BROWSER_RETRY_SECONDS = 5

# Largest single scalar-voltage nudge the API will accept. A backstop against a
# buggy or runaway client: big moves should use an absolute voltage_v, not one
# giant delta. The operator terminal stages much smaller steps than this.
MAX_SCALAR_DELTA_V = 1.0
# A scalar write is "confirmed" when the controller reads back within this of
# the target — wide enough to absorb the Classic's 0.1 V register granularity,
# tighter than a single nudge step.
SCALAR_CONFIRM_TOLERANCE_V = 0.06

# Largest single CCL-budget-fraction nudge the API will accept (0.25 = 25
# percentage points). The operator terminal steps 5 points at a time.
MAX_CCL_SCALING_DELTA = 0.25

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DisplayResponse:
    status: HTTPStatus
    content_type: str
    body: bytes


class SnapshotCache:
    def __init__(self, allocation_override=None) -> None:
        self._snapshot: SupervisorSnapshot | None = None
        self._load_summary: LoadSummary | None = None
        self._allocation: dict | None = None
        self._allocation_override = allocation_override
        self._lock = Lock()

    def set(
        self,
        snapshot: SupervisorSnapshot,
        load_summary: LoadSummary | None = None,
        allocation: dict | None = None,
    ) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._load_summary = load_summary
            self._allocation = allocation

    def get(self) -> SupervisorSnapshot:
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("no supervisor snapshot has been captured yet")
            return self._snapshot

    def get_load_summary(self) -> LoadSummary | None:
        with self._lock:
            return self._load_summary

    def get_allocation(self) -> dict | None:
        with self._lock:
            if self._allocation is None:
                return None
            if self._allocation_override is None:
                return self._allocation
            status = self._allocation_override.status()
            result = {**self._allocation, "allocator_paused": status["paused"]}
            manual_limits = status["manual_limits_a"]
            from .charge_allocator import AllocationOverride

            targets = {}
            for name, target in (result.get("targets") or {}).items():
                try:
                    idx = AllocationOverride.CONTROLLER_NAMES.index(name)
                    ceiling = manual_limits.get(str(idx))
                except ValueError:
                    ceiling = None
                targets[name] = {**target, "manual_ceiling_a": ceiling}
            result["targets"] = targets
            return result


def is_kindle_user_agent(user_agent: str) -> bool:
    normalized = user_agent.lower()
    return "kindle" in normalized or "silk/" in normalized


def format_kindle_time(captured_at: datetime) -> str:
    return captured_at.astimezone().strftime("%H:%M:%S %Z")


KINDLE_RETRY_SECONDS = 5
KINDLE_LIVE_SENTINEL = "offgrid-live"
# How often the Kindle forces a full-screen flash to clear e-ink ghosting that
# accumulates from in-place (partial-update) refreshes. A visible black blink, so
# this is a comfort-vs-cruft tradeoff; tune against the actual panel.
KINDLE_FULL_REFRESH_SECONDS = 900


def _kindle_refresh_script(refresh_seconds: int) -> str:
    """In-place XHR refresher for the Kindle wall display.

    Replaces <meta refresh>: navigation-based refresh dies permanently if
    one reload lands while the server is down (the browser swaps in its
    native error page, which carries no refresh). This script never
    navigates -- it fetches the page and swaps document.body.innerHTML on
    success, so the display self-recovers from Pi reboots, supervisor
    restarts, and Wi-Fi drops. ES3-only for the Kindle Touch's 2011 WebKit.

    Adaptive cadence (recursive setTimeout, not a fixed setInterval): when
    the fetched page is the live dashboard (carries KINDLE_LIVE_SENTINEL),
    refresh slowly (refresh_seconds). When it is anything else -- the nginx
    "stand by" page, the snapshot-unavailable page, a non-200, or a failed
    fetch -- retry fast (KINDLE_RETRY_SECONDS) so recovery after a restart
    is seconds, not a full slow cycle. The injected stand-by page's own
    <meta refresh> is inert (it is swapped in as body content, never
    navigated to), so this timer is the only thing that drives recovery.

    Periodically (FLASH_MS) it also paints the whole screen black then white to
    force a full e-ink waveform refresh, clearing the grey ghosting that
    partial (innerHTML-swap) updates accumulate. The overlay is sized by padding
    (a property this 2011 WebKit honors) to the measured viewport height.
    """
    return (
        '<script type="text/javascript">\n'
        "(function() {\n"
        f"  var LIVE_MS = {refresh_seconds * 1000}, RETRY_MS = {KINDLE_RETRY_SECONDS * 1000};\n"
        f"  var FLASH_MS = {KINDLE_FULL_REFRESH_SECONDS * 1000};\n"
        f"  var SENTINEL = '{KINDLE_LIVE_SENTINEL}';\n"
        "  var lastFlash = (new Date()).getTime();\n"
        "  // Full-screen black->white flash forces a full e-ink refresh, clearing\n"
        "  // ghosting. The overlay is position:fixed, sized by padding to\n"
        "  // clientHeight (NOT overshot: overshoot extends the document and adds a\n"
        "  // scrollbar here). It covers the changing content area, which is where\n"
        "  // ghosting accumulates; the static bottom strip never updates anyway.\n"
        "  function fullRefresh() {\n"
        "    var de = document.documentElement;\n"
        "    var vh = (de && de.clientHeight) || 800;\n"
        "    var vw = (de && de.clientWidth) || 600;\n"
        "    var o = document.createElement('div');\n"
        "    o.style.position = 'fixed';\n"
        "    o.style.left = '0';\n"
        "    o.style.top = '0';\n"
        "    o.style.width = vw + 'px';\n"
        "    o.style.zIndex = '9999';\n"
        "    o.style.background = '#000';\n"
        "    o.style.paddingBottom = vh + 'px';\n"
        "    document.body.appendChild(o);\n"
        "    o.offsetHeight;\n"  # force the black paint
        "    setTimeout(function() {\n"
        "      o.style.background = '#fff';\n"
        "      o.offsetHeight;\n"  # force the white paint
        "      setTimeout(function() { if (o.parentNode) { o.parentNode.removeChild(o); } }, 400);\n"
        "    }, 600);\n"
        "  }\n"
        "  function schedule(ms) { setTimeout(tick, ms); }\n"
        "  function tick() {\n"
        "    var nowMs = (new Date()).getTime();\n"
        "    var x = new XMLHttpRequest();\n"
        "    var url = window.location.pathname + '?k=' + nowMs;\n"
        "    x.open('GET', url, true);\n"
        "    x.onreadystatechange = function() {\n"
        "      if (x.readyState !== 4) { return; }\n"
        "      if (x.status !== 200) { schedule(RETRY_MS); return; }\n"
        "      var t = x.responseText;\n"
        "      // Markers built by concatenation so this script's own source\n"
        "      // can never match them (it lives in the fetched head).\n"
        "      var bo = '<bo' + 'dy';\n"
        "      var bc = '</bo' + 'dy>';\n"
        "      var i = t.indexOf(bo);\n"
        "      if (i < 0) { schedule(RETRY_MS); return; }\n"
        "      i = t.indexOf('>', i);\n"
        "      var j = t.lastIndexOf(bc);\n"
        "      if (i < 0 || j < 0 || j <= i) { schedule(RETRY_MS); return; }\n"
        "      document.body.innerHTML = t.substring(i + 1, j);\n"
        "      if (nowMs - lastFlash >= FLASH_MS) { lastFlash = nowMs; fullRefresh(); }\n"
        "      schedule(t.indexOf(SENTINEL) >= 0 ? LIVE_MS : RETRY_MS);\n"
        "    };\n"
        "    x.onerror = function() { schedule(RETRY_MS); };\n"
        "    x.send(null);\n"
        "  }\n"
        "  schedule(LIVE_MS);\n"
        "})();\n"
        "</script>"
    )


def _browser_refresh_script(refresh_seconds: int) -> str:
    return (
        '<script type="text/javascript">\n'
        "(function() {\n"
        f"  var LIVE_MS = {refresh_seconds * 1000}, RETRY_MS = {BROWSER_RETRY_SECONDS * 1000};\n"
        "  function schedule(ms) { setTimeout(tick, ms); }\n"
        "  function tick() {\n"
        "    var x = new XMLHttpRequest();\n"
        "    x.open('GET', window.location.pathname + '?k=' + (new Date()).getTime(), true);\n"
        "    x.onreadystatechange = function() {\n"
        "      if (x.readyState !== 4) { return; }\n"
        "      if (x.status !== 200) { schedule(RETRY_MS); return; }\n"
        "      var t = x.responseText;\n"
        "      var bo = '<bo' + 'dy';\n"
        "      var bc = '</bo' + 'dy>';\n"
        "      var i = t.indexOf(bo);\n"
        "      if (i < 0) { schedule(RETRY_MS); return; }\n"
        "      i = t.indexOf('>', i);\n"
        "      var j = t.lastIndexOf(bc);\n"
        "      if (i < 0 || j < 0 || j <= i) { schedule(RETRY_MS); return; }\n"
        "      document.body.innerHTML = t.substring(i + 1, j);\n"
        "      schedule(LIVE_MS);\n"
        "    };\n"
        "    x.onerror = function() { schedule(RETRY_MS); };\n"
        "    x.send(null);\n"
        "  }\n"
        "  schedule(LIVE_MS);\n"
        "})();\n"
        "</script>"
    )


def render_kindle_snapshot(
    snapshot: SupervisorSnapshot,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    load_summary: LoadSummary | None = None,
    allocation: dict | None = None,
) -> str:
    status = snapshot_severity_text(snapshot)
    updated = format_kindle_time(snapshot.captured_at)
    soc_text = _soc_text(snapshot)
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        _kindle_refresh_script(refresh_seconds),
        "<title>Off-Grid Power</title>",
        "<style>",
        "html,body{height:100%;}",
        "body{font-family:serif;color:#000;background:#fff;margin:4px;font-size:17px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
        "h2{font-size:19px;margin:8px 0 2px 0;border-bottom:1px solid #000;}",
        "ul{margin:0 0 4px 18px;padding:0;}",
        "li{line-height:1.15;}",
        "table{border-collapse:collapse;width:100%;}",
        # No grey cell dividers: e-ink ghosts hardest on these persistent light
        # lines. Bold first-column labels carry the row structure instead.
        "td{font-size:17px;line-height:1.25;padding:1px 0;vertical-align:top;}",
        "td:first-child{font-size:17px;font-weight:bold;width:32%;}",
        ".bad{font-weight:bold;}",
        ".summary-table{margin:0 0 6px 0;border-bottom:1px solid #000;}",
        ".summary-table td{font-size:19px;font-weight:bold;border-bottom:0;padding:0 0 2px 0;}",
        ".summary-table .soc-cell{font-size:36px;line-height:1;text-align:left;vertical-align:middle;width:32%;}",
        ".summary-table .meta-cell{font-size:17px;line-height:1.05;text-align:left;vertical-align:middle;width:52%;}",
        ".summary-table .button-cell{font-size:17px;line-height:1;text-align:right;vertical-align:middle;width:16%;}",
        ".top-link{font-size:17px;line-height:2.1;color:#000;text-decoration:none;border:1px solid #000;padding:0 10px;display:block;text-align:center;}",
        # Plaintext nav hints after the last content, pointing to the invisible
        # page-turn tap zones at the margins (no footer button — this browser
        # can't reliably pin one to the screen bottom).
        ".nav-hint{font-size:17px;font-weight:bold;margin:10px 0 0 0;}",
        # The tap strip's height comes from padding-bottom — the one sizing
        # property this 2011 WebKit honors on a fixed box (height: and bottom:0
        # were both ignored / anchored to the short content box, so the strips
        # fell short of the screen). top 58 + ~660 padding reaches past the 700px
        # viewport bottom; fixed keeps it out of flow so it adds no scroll.
        ".page-turn{position:fixed;top:58px;padding-bottom:640px;width:18%;z-index:10;text-indent:-9999px;overflow:hidden;}",
        ".page-turn-left{left:0;}",
        ".page-turn-right{right:0;}",
        ".small{font-size:13px;}",
        "</style>",
        "</head>",
        "<body>",
        f"<!-- {KINDLE_LIVE_SENTINEL} -->",
        _page_turn_link("/kindle/weather", "left", "Weather"),
        _page_turn_link("/kindle/details", "right", "More Power Info"),
        '<table class="summary-table">',
        f'<tr><td class="soc-cell">SOC {escape(soc_text)}</td><td class="meta-cell">Updated: {escape(updated)}<br>Status: {escape(status)}</td><td class="button-cell"><a class="top-link" href="/kindle/weather">Weather</a></td></tr>',
        "</table>",
    ]
    lines.extend(_load_section(load_summary))
    lines.extend(_battery_section(snapshot))
    lines.extend(_charge_controller_sections(snapshot, allocation=allocation, include_settings=True))
    lines.extend(_status_summary_section(snapshot))
    lines.extend(_kindle_nav_hint("MORE >", "right"))
    lines.extend(["</body>", "</html>"])
    return "\n".join(lines)


def render_kindle_details(
    snapshot: SupervisorSnapshot,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    allocation: dict | None = None,
) -> str:
    status = snapshot_severity_text(snapshot)
    updated = format_kindle_time(snapshot.captured_at)
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        _kindle_refresh_script(refresh_seconds),
        "<title>Off-Grid Power Details</title>",
        "<style>",
        "html,body{height:100%;}",
        "body{font-family:serif;color:#000;background:#fff;margin:4px;font-size:17px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
        "h2{font-size:19px;margin:8px 0 2px 0;border-bottom:1px solid #000;}",
        "ul{margin:0 0 4px 18px;padding:0;}",
        "li{line-height:1.15;}",
        "table{border-collapse:collapse;width:100%;}",
        # No grey cell dividers: e-ink ghosts hardest on these persistent light
        # lines. Bold first-column labels carry the row structure instead.
        "td{font-size:17px;line-height:1.25;padding:1px 0;vertical-align:top;}",
        "td:first-child{font-size:17px;font-weight:bold;width:32%;}",
        ".summary-table{margin:0 0 6px 0;border-bottom:1px solid #000;}",
        ".summary-table td{font-size:19px;font-weight:bold;border-bottom:0;padding:0 0 2px 0;}",
        ".summary-table .soc-cell{font-size:30px;line-height:1;text-align:left;vertical-align:middle;width:32%;}",
        ".summary-table .meta-cell{font-size:17px;line-height:1.05;text-align:left;vertical-align:middle;width:52%;}",
        ".summary-table .button-cell{font-size:17px;line-height:1;text-align:right;vertical-align:middle;width:16%;}",
        ".top-link{font-size:17px;line-height:2.1;color:#000;text-decoration:none;border:1px solid #000;padding:0 10px;display:block;text-align:center;}",
        # Plaintext nav hints after the last content, pointing to the invisible
        # page-turn tap zones at the margins (no footer button — this browser
        # can't reliably pin one to the screen bottom).
        ".nav-hint{font-size:17px;font-weight:bold;margin:10px 0 0 0;}",
        # The tap strip's height comes from padding-bottom — the one sizing
        # property this 2011 WebKit honors on a fixed box (height: and bottom:0
        # were both ignored / anchored to the short content box, so the strips
        # fell short of the screen). top 58 + ~660 padding reaches past the 700px
        # viewport bottom; fixed keeps it out of flow so it adds no scroll.
        ".page-turn{position:fixed;top:58px;padding-bottom:640px;width:18%;z-index:10;text-indent:-9999px;overflow:hidden;}",
        ".page-turn-left{left:0;}",
        ".page-turn-right{right:0;}",
        ".small{font-size:13px;}",
        "</style>",
        "</head>",
        "<body>",
        f"<!-- {KINDLE_LIVE_SENTINEL} -->",
        _page_turn_link("/kindle", "left", "Back to Power"),
        _page_turn_link("/kindle/weather", "right", "Weather"),
        '<table class="summary-table">',
        f'<tr><td class="soc-cell">Details</td><td class="meta-cell">Updated: {escape(updated)}<br>Status: {escape(status)}</td><td class="button-cell"><a class="top-link" href="/kindle/weather">Weather</a></td></tr>',
        "</table>",
    ]
    lines.extend(_inverter_charger_section(snapshot))
    lines.extend(_temperature_section(snapshot))
    lines.extend(_kindle_nav_hint("< BACK", "left"))
    lines.extend(["</body>", "</html>"])
    return "\n".join(lines)


def render_kindle_weather(
    payload: dict | None,
    refresh_seconds: int = KINDLE_REFRESH_SECONDS,
    now: datetime | None = None,
    annotations: list[str] | None = None,
) -> str:
    # Consumes the normalized weather API payload (weather.weather_api_payload),
    # so this renderer holds no knowledge of the upstream weather provider.
    payload = payload or {}
    reference = now or datetime.now().astimezone()
    current = payload.get("current")
    observed_at = _parse_payload_time(payload.get("observed_at"))
    stale = bool(payload.get("stale"))
    error = payload.get("error")
    label = payload.get("label") or "Weather"
    status_text = "Weather unavailable"
    updated = "never"
    if current:
        updated = format_kindle_time(observed_at) if observed_at is not None else "never"
        status_text = "stale forecast" if stale else "forecast"
    too_stale = (
        bool(current)
        and stale
        and observed_at is not None
        and reference.astimezone() - observed_at.astimezone() >= WEATHER_STALE_AFTER
    )
    # "OFFLINE as of 13:35" scans better than "As of: 13:35 (OFFLINE)" and fits the
    # Kindle header cell without wrapping. All data here is internet-sourced so
    # the context makes "OFFLINE" unambiguous without qualification.
    timestamp_line = (f"OFFLINE as of {escape(updated)}" if annotations else f"As of: {escape(updated)}")
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        _kindle_refresh_script(refresh_seconds),
        "<title>Off-Grid Weather</title>",
        "<style>",
        "body{font-family:serif;color:#000;background:#fff;margin:4px;font-size:17px;-webkit-text-size-adjust:100%;text-size-adjust:100%;}",
        "h2{font-size:19px;margin:8px 0 2px 0;border-bottom:1px solid #000;}",
        "table{border-collapse:collapse;width:100%;}",
        # No grey cell dividers: e-ink ghosts hardest on these persistent light
        # lines. Bold first-column labels carry the row structure instead.
        "td{font-size:17px;line-height:1.25;padding:1px 0;vertical-align:top;}",
        "td:first-child{font-size:17px;font-weight:bold;width:38%;}",
        ".summary-table{margin:0 0 6px 0;border-bottom:1px solid #000;}",
        ".summary-table td{font-size:19px;font-weight:bold;border-bottom:0;padding:0 0 2px 0;}",
        ".summary-table .weather-cell{font-size:30px;line-height:1;text-align:left;vertical-align:middle;width:38%;}",
        ".summary-table .meta-cell{font-size:17px;line-height:1.05;text-align:left;vertical-align:middle;width:46%;}",
        ".summary-table .button-cell{font-size:17px;line-height:1;text-align:right;vertical-align:middle;width:16%;}",
        ".top-link{font-size:17px;line-height:2.1;color:#000;text-decoration:none;border:1px solid #000;padding:0 10px;display:block;text-align:center;}",
        # The tap strip's height comes from padding-bottom — the one sizing
        # property this 2011 WebKit honors on a fixed box (height: and bottom:0
        # were both ignored / anchored to the short content box, so the strips
        # fell short of the screen). top 58 + ~660 padding reaches past the 700px
        # viewport bottom; fixed keeps it out of flow so it adds no scroll.
        ".page-turn{position:fixed;top:58px;padding-bottom:640px;width:18%;z-index:10;text-indent:-9999px;overflow:hidden;}",
        ".page-turn-left{left:0;}",
        ".page-turn-right{right:0;}",
        ".small{font-size:13px;}",
        "</style>",
        "</head>",
        "<body>",
        f"<!-- {KINDLE_LIVE_SENTINEL} -->",
        _page_turn_link("/kindle/details", "left", "Power Details"),
        _page_turn_link("/kindle", "right", "Power"),
    ]
    if too_stale:
        lines.extend(
            [
                '<table class="summary-table">',
                f'<tr><td class="weather-cell">Weather</td><td class="meta-cell">Weather service has been unreachable since {escape(format_time(observed_at))}</td><td class="button-cell"><a class="top-link" href="/kindle">Power</a></td></tr>',
                "</table>",
                "<h2>Conditions</h2>",
                "<p>Weather service unreachable.</p>",
            ]
        )
        if error:
            lines.append(f'<p class="small">{escape(error)}</p>')
    elif not current:
        lines.extend(
            [
                '<table class="summary-table">',
                f'<tr><td class="weather-cell">Weather</td><td class="meta-cell">{timestamp_line}<br>{escape(status_text)}</td><td class="button-cell"><a class="top-link" href="/kindle">Power</a></td></tr>',
                "</table>",
                "<h2>Conditions</h2>",
                "<p>Weather unavailable.</p>",
            ]
        )
        if error:
            lines.append(f'<p class="small">{escape(error)}</p>')
    else:
        temp = _format_number(current.get("temperature_c"), "C", decimals=1)
        condition = (current.get("condition") or {}).get("text") or "unknown"
        lines.extend(
            [
                '<table class="summary-table">',
                f'<tr><td class="weather-cell">{escape(temp or "--")}</td><td class="meta-cell">{escape(label)}: {escape(condition)}<br>{timestamp_line}</td><td class="button-cell"><a class="top-link" href="/kindle">Power</a></td></tr>',
                "</table>",
                "<h2>Current</h2>",
                "<table>",
                _weather_row("Feels Like", _format_number(current.get("apparent_temperature_c"), "C", decimals=1)),
                _weather_row("Humidity", _format_number(current.get("humidity_pct"), "%", decimals=0)),
                _weather_row("Cloud", _format_number(current.get("cloud_cover_pct"), "%", decimals=0)),
                _weather_row("Wind", _wind_text(current.get("wind") or {})),
                _weather_row("Precip Now", _precip_text(current)),
                "</table>",
            ]
        )
        lines.extend(_hourly_weather_section(payload.get("hourly") or []))
        lines.extend(_daily_weather_section(payload.get("daily") or []))
        lines.extend(_solar_irradiance_section(current.get("irradiance") or {}))
        lines.extend(_astronomy_weather_section(payload.get("astronomy") or {}))
        if stale:
            lines.append("<p class=\"small\">Using last cached weather. WAN fetch failed.</p>")
        if error:
            lines.append(f'<p class="small">{escape(error)}</p>')

    lines.extend(
        [
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(lines)


def render_browser_snapshot(
    snapshot: SupervisorSnapshot,
    load_summary: LoadSummary | None = None,
    allocation: dict | None = None,
) -> str:
    payload = snapshot_api_payload(snapshot, load_summary=load_summary, allocation=allocation)
    rendered = _trim_browser_snapshot_header(render_api_snapshot(payload))
    return "\n".join(
        [
            "<!doctype html>",
            "<html>",
            "<head>",
            '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            _browser_refresh_script(BROWSER_POWER_REFRESH_SECONDS),
            "<title>Off-Grid Power</title>",
            "<style>",
            _browser_display_css(),
            "</style>",
            "</head>",
            "<body>",
            _browser_snapshot_header(payload),
            f"<pre>{escape(rendered)}</pre>",
            "</body>",
            "</html>",
        ]
    )


def _browser_snapshot_header(payload: dict) -> str:
    battery = payload.get("battery") or {}
    status = payload.get("status") or {}
    captured_at = _parse_payload_time(payload.get("captured_at"))
    soc = battery.get("soc_percent")
    soc_text = "--" if soc is None else f"{soc}%"
    updated = format_updated_time(captured_at) if captured_at is not None else "unavailable"
    severity = status.get("severity") or ("OK" if status.get("ok") else "ERROR")
    return (
        '<div class="browser-summary power-summary">'
        f'<div class="primary-cell">SOC {escape(str(soc_text))}</div>'
        f'<div class="meta-cell">Updated: {escape(updated)}<br>Status: {escape(str(severity))}</div>'
        '<div class="button-cell"><a class="nav-button" href="/weather">Weather</a></div>'
        "</div>"
    )


def _trim_browser_snapshot_header(rendered: str) -> str:
    lines = rendered.splitlines()
    if len(lines) >= 4 and lines[0].startswith("Off-Grid Power Supervisor") and lines[3] == "":
        return "\n".join(lines[4:])
    return rendered


def render_browser_weather(payload: dict | None, annotations: list[str] | None = None) -> str:
    payload = payload or {}
    current = payload.get("current")
    observed_at = _parse_payload_time(payload.get("observed_at"))
    stale = bool(payload.get("stale"))
    label = payload.get("label") or "Weather"
    updated = format_updated_time(observed_at) if observed_at is not None else "unavailable"
    status_text = "stale forecast" if stale and current else "forecast" if current else "Weather unavailable"
    primary = "Weather"
    if current:
        primary = _format_number(current.get("temperature_c"), "C", decimals=1) or "--"
        condition = (current.get("condition") or {}).get("text") or "unknown"
        status_text = f"{label}: {condition}"
    annotation_suffix = (" (" + ", ".join(annotations) + ")") if annotations else ""
    rendered = _trim_browser_weather_header(render_api_weather(payload))
    lines = [
        "<!doctype html>",
        "<html>",
        "<head>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        _browser_refresh_script(BROWSER_WEATHER_REFRESH_SECONDS),
        "<title>Off-Grid Weather</title>",
        "<style>",
        _browser_display_css(),
        "</style>",
        "</head>",
        "<body>",
        _browser_weather_header(primary, updated + annotation_suffix, status_text, "/"),
        f"<pre>{escape(rendered)}</pre>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines)


def _browser_weather_header(primary: str, updated: str, status: str, power_href: str) -> str:
    return (
        '<div class="browser-summary weather-summary">'
        f'<div class="primary-cell">{escape(primary)}</div>'
        f'<div class="meta-cell">As of: {escape(updated)}<br>{escape(status)}</div>'
        f'<div class="button-cell"><a class="nav-button" href="{escape(power_href)}">Power</a></div>'
        "</div>"
    )


def _trim_browser_weather_header(rendered: str) -> str:
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if line == "":
            return "\n".join(lines[index + 1 :])
    return rendered


def _browser_display_css() -> str:
    return (
        "body{font-family:monospace;background:#111;color:#eee;margin:12px;}"
        "a{color:#9cf;}"
        ".nav{font:16px/1.25 monospace;margin:0 0 12px 0;text-align:right;}"
        ".nav-button{font:16px/1.25 monospace;color:#eee;text-decoration:none;border:1px solid #777;"
        "background:#222;padding:5px 12px;display:inline-block;}"
        ".nav-button:hover{background:#333;border-color:#aaa;}"
        ".browser-summary{display:grid;grid-template-columns:24ch minmax(0,1fr) auto;align-items:center;"
        "font:16px/1.15 monospace;font-weight:bold;border-bottom:1px solid #777;margin:0 0 12px 0;padding:0 0 6px 0;}"
        ".browser-summary .primary-cell{font-size:36px;line-height:1;color:#fff;white-space:nowrap;}"
        ".browser-summary .meta-cell{text-align:left;}"
        ".browser-summary .button-cell{text-align:right;white-space:nowrap;}"
        "table{border-collapse:collapse;width:100%;}"
        # The terminal block. pre-wrap on the desktop (wide) view; the narrow
        # media query below overrides it. Defined here (not per-page) so it sits
        # ahead of the @media rule in source order and the override wins.
        "pre{font:16px/1.25 monospace;white-space:pre-wrap;margin:0;}"
        # On a phone the 3-up grid can't fit the 24ch primary cell, the meta
        # column, and the nav button on one row. Collapse to two columns: the
        # primary value spans the top row, with the meta text and nav button
        # sharing the row below. width=device-width (set in the page head) is
        # what makes this media query fire at the real screen size.
        "@media (max-width:480px){"
        "body{margin:8px;}"
        ".browser-summary{grid-template-columns:minmax(0,1fr) auto;}"
        ".browser-summary .primary-cell{grid-column:1 / -1;font-size:30px;margin:0 0 4px 0;}"
        # The widest terminal row is ~63 monospace chars — ~600px at 16px, far
        # past a phone's ~390px. Shrink the type to fit the viewport with the
        # columns intact: white-space:pre (NOT pre-wrap) so a row never breaks
        # mid-value, and a vw-scaled size so the widest row lands inside the
        # screen. overflow-x is a safety net for any row the clamp can't quite
        # fit; pinch-zoom (left enabled) is then for reading, not for fitting.
        "pre{white-space:pre;font-size:clamp(8px,2.4vw,13px);overflow-x:auto;}"
        "}"
    )


# Views whose ?refresh=1 queues an out-of-cycle poll of the local power
# sources. Health is excluded so it stays a cheap liveness check.
_SOURCE_REFRESH_PATHS = {"/api/v1/snapshot", "/", "/kindle", "/kindle/details", "/display"}
# Weather views whose ?refresh=1 queues a background re-fetch of the forecast.
_WEATHER_REFRESH_PATHS = {"/api/v1/weather", "/weather", "/kindle/weather"}
# Paths that need the weather forecast fetched in order to render.
_WEATHER_VIEW_PATHS = {"/api/v1/weather", "/weather", "/kindle/weather"}


def _has_refresh_flag(query: str) -> bool:
    return "1" in parse_qs(query).get("refresh", [])


def wants_source_refresh(path: str) -> bool:
    parsed = urlparse(path)
    return parsed.path in _SOURCE_REFRESH_PATHS and _has_refresh_flag(parsed.query)


def wants_weather_refresh(path: str) -> bool:
    parsed = urlparse(path)
    return parsed.path in _WEATHER_REFRESH_PATHS and _has_refresh_flag(parsed.query)


def route_display_request(
    snapshot: SupervisorSnapshot,
    path: str,
    user_agent: str,
    load_summary: LoadSummary | None = None,
    weather_report: WeatherReport | None = None,
    refresh_hook: Callable[[], None] | None = None,
    weather_refresh_hook: Callable[[], None] | None = None,
    allocation: dict | None = None,
) -> DisplayResponse:
    # Both hooks only queue work for next time; this response still carries the
    # current snapshot/cached forecast, so a slow source never delays the reply.
    if refresh_hook is not None and wants_source_refresh(path):
        refresh_hook()
    if weather_refresh_hook is not None and wants_weather_refresh(path):
        weather_refresh_hook()
    parsed_path = urlparse(path).path
    if parsed_path in {"/api/v1/health", "/api/v1/snapshot"}:
        return route_api_request(snapshot, parsed_path, load_summary=load_summary, allocation=allocation)
    if parsed_path == "/api/v1/weather":
        return _json_response(HTTPStatus.OK, weather_api_payload(weather_report))
    if parsed_path not in {"/", "/kindle", "/kindle/details", "/kindle/weather", "/display", "/weather", "/healthz"}:
        return DisplayResponse(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
    if parsed_path == "/healthz":
        # Liveness probe: reaching here means the supervisor produced a snapshot,
        # so the process and its poll loop are alive. Offline devices are a
        # degraded condition surfaced by /api/v1/health, not a liveness failure —
        # restarting the supervisor would not bring an offline device back. The
        # "cannot produce a snapshot at all" case is handled upstream in the
        # server, which returns 503 before routing reaches here.
        return DisplayResponse(HTTPStatus.OK, "text/plain; charset=utf-8", b"ok\n")
    is_kindle = is_kindle_user_agent(user_agent)
    content_type = "text/html; charset=utf-8"

    # The /kindle* paths ARE the Kindle interface: they always serve
    # Kindle-formatted content, irrespective of user-agent. The wall Kindle's
    # jailbroken browser does not reliably advertise a recognizable UA, so the
    # explicit path -- not UA sniffing -- is the contract for these.
    if parsed_path == "/kindle":
        html = render_kindle_snapshot(snapshot, load_summary=load_summary, allocation=allocation)
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    if parsed_path == "/kindle/details":
        html = render_kindle_details(snapshot, allocation=allocation)
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    if parsed_path == "/kindle/weather":
        html = render_kindle_weather(weather_api_payload(weather_report), annotations=snapshot_status_annotations(snapshot))
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))

    # "/", "/display", "/weather": shared paths, negotiated by user-agent
    # (browser by default; Kindle markup when the UA is recognized).
    if parsed_path == "/weather":
        payload = weather_api_payload(weather_report)
        annotations = snapshot_status_annotations(snapshot)
        html = render_kindle_weather(payload, annotations=annotations) if is_kindle else render_browser_weather(payload, annotations=annotations)
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    if is_kindle:
        html = render_kindle_snapshot(snapshot, load_summary=load_summary, allocation=allocation)
        return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))
    html = render_browser_snapshot(snapshot, load_summary=load_summary, allocation=allocation)
    return DisplayResponse(HTTPStatus.OK, content_type, html.encode("utf-8"))


def route_api_request(
    snapshot: SupervisorSnapshot,
    path: str,
    load_summary: LoadSummary | None = None,
    now: datetime | None = None,
    allocation: dict | None = None,
) -> DisplayResponse:
    if path == "/api/v1/health":
        payload = health_api_payload(snapshot, now=now)
        # Only a critical ERROR fails the check (503). A degraded WARNING — an
        # offline device or a non-critical condition — stays 200 so monitors
        # don't treat "one controller offline" the same as "supervisor down".
        status = HTTPStatus.SERVICE_UNAVAILABLE if snapshot.status_text == STATUS_ERROR else HTTPStatus.OK
        return _json_response(status, payload)
    if path == "/api/v1/snapshot":
        return _json_response(
            HTTPStatus.OK,
            snapshot_api_payload(snapshot, load_summary=load_summary, now=now, allocation=allocation),
        )
    return _json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})


def _control_relay(relay_controller, payload: dict) -> DisplayResponse:
    if relay_controller is None:
        return _json_response(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "relay controller not configured"})
    try:
        name = payload.get("relay")
        on = payload.get("on")
        if not isinstance(on, bool):
            raise ValueError("'on' must be a boolean")
        relay_controller.set(name, on)
        return _json_response(HTTPStatus.OK, {"ok": True, "relay": name, "on": on, "stub": relay_controller.is_stub})
    except (ValueError, KeyError) as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})


def _control_allocation_pause(allocation_override, payload: dict) -> DisplayResponse:
    try:
        if allocation_override is None:
            raise RuntimeError("charge allocation is not enabled; allocation override has no effect")
        if "paused" not in payload:
            raise ValueError("'paused' (bool) is required")
        paused = bool(payload["paused"])
        previous = allocation_override.set_paused(paused)
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    logger.info("Allocation pause write: %s -> %s", previous, paused)
    return _json_response(HTTPStatus.OK, {"ok": True, "previous_paused": previous, "paused": paused})


def _control_allocation_manual_limit(allocation_override, payload: dict) -> DisplayResponse:
    try:
        if allocation_override is None:
            raise RuntimeError("charge allocation is not enabled; allocation override has no effect")
        if "controller" not in payload:
            raise ValueError("'controller' (integer index) is required")
        try:
            index = int(payload["controller"])
        except (TypeError, ValueError):
            raise ValueError(f"'controller' must be an integer, got {payload['controller']!r}")
        limit_a = payload.get("limit_a")
        if limit_a is not None:
            try:
                limit_a = float(limit_a)
            except (TypeError, ValueError):
                raise ValueError(f"'limit_a' must be a number or null, got {limit_a!r}")
        previous = allocation_override.set_manual_limit(index, limit_a)
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    logger.info("Allocation manual limit write: controller %d: %s -> %s", index, previous, limit_a)
    return _json_response(
        HTTPStatus.OK,
        {"ok": True, "controller": index, "previous_limit_a": previous, "limit_a": limit_a},
    )


def route_control_request(
    supervisor: Supervisor,
    path: str,
    payload: dict,
    charge_ceiling=None,
    allocation_override=None,
    relay_controller=None,
) -> DisplayResponse:
    if path == "/api/v1/control/ccl-scaling-factor":
        return _control_ccl_scaling_factor(charge_ceiling, payload)
    if path == "/api/v1/control/charge-controller/voltage":
        return _control_charge_controller_voltage(supervisor, payload)
    if path in ("/api/v1/control/charge-controller/charge-settings", "/api/v1/control/classic/charge-settings", "/api/v1/control/epever/charge-settings"):
        return _control_charge_controller_charge_settings(supervisor, payload, path)
    if path in ("/api/v1/control/charge-controller/charging", "/api/v1/control/epever/charging"):
        return _control_charge_controller_charging(supervisor, payload, path)
    if path in ("/api/v1/control/charge-controller/sync", "/api/v1/control/epever/sync-from-classic"):
        return _control_charge_controller_sync(supervisor, payload, path)
    if path == "/api/v1/control/magnum/charge-settings":
        return _control_magnum_charge_settings(supervisor, payload)
    if path == "/api/v1/control/allocation/pause":
        return _control_allocation_pause(allocation_override, payload)
    if path == "/api/v1/control/allocation/manual-limit":
        return _control_allocation_manual_limit(allocation_override, payload)
    if path == "/api/v1/control/relay":
        return _control_relay(relay_controller, payload)
    return _json_response(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})


def _control_charge_controller_voltage(supervisor: Supervisor, payload: dict) -> DisplayResponse:
    try:
        controller = _controller_number(payload)
        voltage_v = _optional_number(payload, "voltage_v")
        delta_v = _optional_number(payload, "delta_v")
        dry_run = bool(payload.get("dry_run", False))
        if (voltage_v is None) == (delta_v is None):
            raise ValueError("exactly one of voltage_v or delta_v must be supplied")
        if delta_v is not None and abs(delta_v) > MAX_SCALAR_DELTA_V:
            raise ValueError(
                f"delta_v magnitude {delta_v:+.2f}V exceeds the {MAX_SCALAR_DELTA_V:.2f}V "
                "per-call cap; use voltage_v for a larger move"
            )

        if controller == 0:
            return _control_classic_scalar_voltage(supervisor, voltage_v, delta_v, dry_run=dry_run)
        if controller == 1:
            return _control_epever_scalar_voltage(supervisor, voltage_v, delta_v, dry_run=dry_run)
        raise ValueError(f"unknown charge controller number: {controller}")
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})


def _control_ccl_scaling_factor(charge_ceiling, payload: dict) -> DisplayResponse:
    """Set or nudge the CCL scaling factor (allocator policy).

    `charge_ceiling` is the live ChargeCeiling; it is None when charge allocation
    is not running, in which case the knob has no effect and the request is a
    conflict. Accepts exactly one of `factor` (absolute) or `delta`. A successful
    write is persisted by the ceiling's on-change hook, so it survives a
    supervisor restart.
    """
    try:
        if charge_ceiling is None:
            raise RuntimeError("charge allocation is not enabled; CCL scaling factor has no effect")
        factor = _optional_number(payload, "factor")
        delta = _optional_number(payload, "delta")
        dry_run = bool(payload.get("dry_run", False))
        if (factor is None) == (delta is None):
            raise ValueError("exactly one of factor or delta must be supplied")
        if delta is not None and abs(delta) > MAX_CCL_SCALING_DELTA:
            raise ValueError(
                f"delta magnitude {delta:+.2f} exceeds the {MAX_CCL_SCALING_DELTA:.2f} per-call cap"
            )

        previous = round(charge_ceiling.scaling_factor, 4)
        if dry_run:
            target = _validate_ccl_scaling_dry_run(previous, factor, delta)
        elif delta is not None:
            previous, target = charge_ceiling.nudge_scaling_factor(delta)
        else:
            target = charge_ceiling.set_scaling_factor(factor)
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    if not dry_run:
        logger.info(
            "CCL scaling factor write: %.2f->%.2f%s",
            previous,
            target,
            f" (delta {delta:+.2f})" if delta is not None else "",
        )
    return _json_response(
        HTTPStatus.OK,
        {
            "ok": True,
            "previous_factor": previous,
            "factor": target,
            "delta": delta,
            "dry_run": dry_run,
        },
    )


def _validate_ccl_scaling_dry_run(previous: float, factor: float | None, delta: float | None) -> float:
    """Resolve and range-check the would-be CCL scaling factor without writing."""
    from .charge_ceiling import _validate_scaling_factor  # local import avoids a module cycle

    return _validate_scaling_factor(previous + delta if delta is not None else factor)


def _resolve_scalar_target(voltage_v: float | None, delta_v: float | None, base_v: float) -> float:
    """Resolve the absolute scalar target from either an absolute or delta request.

    For a delta, the current setpoint is read fresh from the device (base_v) and
    the delta added — the read-modify-write the operator asked for. The result is
    range-checked here so a delta that lands somewhere absurd is refused the same
    way an absolute request would be.
    """
    target = round(base_v + delta_v, 2) if delta_v is not None else round(voltage_v, 2)
    if not (0.0 < target <= 65.0):
        raise ValueError(f"resulting charge voltage out of range: {target:.2f}V")
    return target


def _control_classic_scalar_voltage(
    supervisor: Supervisor,
    voltage_v: float | None,
    delta_v: float | None,
    *,
    dry_run: bool,
) -> DisplayResponse:
    previous_voltage_v = round(supervisor.read_classic_settings().absorb_voltage_v, 2) if delta_v is not None else None
    base = previous_voltage_v if previous_voltage_v is not None else 0.0
    voltage_v = _resolve_scalar_target(voltage_v, delta_v, base)
    planned = {
        "absorb_voltage_v": round(voltage_v, 2),
        "float_voltage_v": round(voltage_v - 0.1, 2),
        "equalize_voltage_v": round(voltage_v, 2),
        "max_temp_comp_voltage_v": round(voltage_v, 2),
    }
    _guard_voltage_targets_against_bms(supervisor.read_snapshot(), planned)
    settings = None if dry_run else supervisor.write_classic_charge_settings(**planned)
    confirmed = None if settings is None else _scalar_confirmed(settings.absorb_voltage_v, planned["absorb_voltage_v"])
    if settings is not None:
        _log_scalar_change("classic", previous_voltage_v, voltage_v, delta_v, confirmed)
    return _json_response(
        HTTPStatus.OK,
        {
            "ok": True,
            "device": "classic",
            "controller": 0,
            "previous_voltage_v": previous_voltage_v,
            "voltage_v": voltage_v,
            "delta_v": delta_v,
            "confirmed": confirmed,
            "dry_run": dry_run,
            "planned": planned,
            "settings": _classic_settings_api_payload(settings),
        },
    )


def _control_epever_scalar_voltage(
    supervisor: Supervisor,
    voltage_v: float | None,
    delta_v: float | None,
    *,
    dry_run: bool,
) -> DisplayResponse:
    previous_voltage_v = round(supervisor.read_epever_settings().boost_voltage_v, 2) if delta_v is not None else None
    base = previous_voltage_v if previous_voltage_v is not None else 0.0
    voltage_v = _resolve_scalar_target(voltage_v, delta_v, base)
    planned = {
        "boost_voltage_v": round(voltage_v, 2),
        "absorb_voltage_v": round(voltage_v, 2),
        "float_voltage_v": round(voltage_v, 2),
        "equalize_voltage_v": round(voltage_v, 2),
        "boost_reconnect_voltage_v": round(voltage_v - 1.0, 2),
        "bulk_recovery_voltage_v": round(voltage_v - 1.0, 2),
    }
    snapshot = supervisor.read_snapshot()
    _guard_voltage_targets_against_bms(
        snapshot,
        {
            "boost_voltage_v": planned["boost_voltage_v"],
            "float_voltage_v": planned["float_voltage_v"],
            "equalize_voltage_v": planned["equalize_voltage_v"],
        },
    )
    settings = None
    if not dry_run:
        settings = supervisor.write_epever_charge_voltages(
            boost_v=planned["boost_voltage_v"],
            float_v=planned["float_voltage_v"],
            equalize_v=planned["equalize_voltage_v"],
            boost_reconnect_v=planned["boost_reconnect_voltage_v"],
        )
    confirmed = None if settings is None else _scalar_confirmed(settings.boost_voltage_v, planned["boost_voltage_v"])
    if settings is not None:
        _log_scalar_change("epever", previous_voltage_v, voltage_v, delta_v, confirmed)
    return _json_response(
        HTTPStatus.OK,
        {
            "ok": True,
            "device": "epever",
            "controller": 1,
            "previous_voltage_v": previous_voltage_v,
            "voltage_v": voltage_v,
            "delta_v": delta_v,
            "confirmed": confirmed,
            "dry_run": dry_run,
            "planned": planned,
            "settings": _epever_settings_api_payload(settings),
        },
    )


def _scalar_confirmed(readback_v: float, target_v: float) -> bool:
    return abs(readback_v - target_v) <= SCALAR_CONFIRM_TOLERANCE_V


def _log_scalar_change(
    device: str,
    previous_v: float | None,
    voltage_v: float,
    delta_v: float | None,
    confirmed: bool,
) -> None:
    """Leave an audit trail for operator scalar-voltage writes in the journal.

    Manual nudges and absolute sets should be as visible in the logs as the
    allocator's automated writes, so a later "why did the setpoint move?" has an
    answer. Goes through the stdlib logger the supervisor service hands to
    journald.
    """
    move = f"{previous_v:.2f}->{voltage_v:.2f}V" if previous_v is not None else f"{voltage_v:.2f}V"
    delta = f" (delta {delta_v:+.2f}V)" if delta_v is not None else ""
    logger.info("scalar charge voltage write: %s %s%s confirmed=%s", device, move, delta, confirmed)


def _control_charge_controller_charge_settings(supervisor: Supervisor, payload: dict, path: str) -> DisplayResponse:
    # Brand-name paths are aliases; canonical path requires "controller" number.
    if path == "/api/v1/control/classic/charge-settings":
        controller = 0
    elif path == "/api/v1/control/epever/charge-settings":
        controller = 1
    else:
        try:
            controller = _controller_number(payload)
        except ValueError as exc:
            return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    if controller == 0:
        return _control_classic_charge_settings(supervisor, payload)
    if controller == 1:
        return _control_epever_charge_settings(supervisor, payload)
    return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"unknown charge controller number: {controller}"})


def _control_charge_controller_charging(supervisor: Supervisor, payload: dict, path: str) -> DisplayResponse:
    if path == "/api/v1/control/epever/charging":
        controller = 1
    else:
        try:
            controller = _controller_number(payload)
        except ValueError as exc:
            return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    if controller == 1:
        return _control_epever_charging(supervisor, payload)
    return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"controller {controller} does not support charging toggle"})


def _control_charge_controller_sync(supervisor: Supervisor, payload: dict, path: str) -> DisplayResponse:
    # Canonical: {"source": 0, "target": 1}. Legacy path implies source=0, target=1.
    if path == "/api/v1/control/epever/sync-from-classic":
        source, target = 0, 1
    else:
        try:
            source = int(payload.get("source", -1))
            target = int(payload.get("target", -1))
        except (TypeError, ValueError):
            return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "source and target must be controller numbers"})
        if source < 0 or target < 0:
            return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "source and target controller numbers are required"})
    if source == 0 and target == 1:
        return _control_epever_sync_from_classic(supervisor, payload)
    return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"sync from controller {source} to controller {target} is not supported"})


def _control_classic_charge_settings(supervisor: Supervisor, payload: dict) -> DisplayResponse:
    try:
        absorb_time_minutes = _optional_number(payload, "absorb_time_minutes")
        targets = {
            "battery_current_limit_a": _optional_number(payload, "current_limit_a"),
            "absorb_voltage_v": _optional_number(payload, "absorb_voltage_v"),
            "float_voltage_v": _optional_number(payload, "float_voltage_v"),
            "equalize_voltage_v": _optional_number(payload, "equalize_voltage_v"),
            "absorb_time_s": None if absorb_time_minutes is None else round(absorb_time_minutes * 60),
            "max_temp_comp_voltage_v": _optional_number(payload, "max_temp_comp_voltage_v"),
        }
        targets = {key: value for key, value in targets.items() if value is not None}
        if not targets:
            return _json_response(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "device": "classic", "error": "no Classic charge settings supplied"},
            )
        if absorb_time_minutes is not None and not (0 <= absorb_time_minutes <= 24 * 60):
            raise ValueError(f"Classic absorb_time_minutes out of range: {absorb_time_minutes}")
        voltage_targets = {
            key: value
            for key, value in targets.items()
            if key in {"absorb_voltage_v", "float_voltage_v", "equalize_voltage_v", "max_temp_comp_voltage_v"}
        }
        if voltage_targets:
            _guard_voltage_targets_against_bms(supervisor.read_snapshot(), voltage_targets)
        settings = supervisor.write_classic_charge_settings(**targets)
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "device": "classic", "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "device": "classic", "error": str(exc)})
    return _json_response(
        HTTPStatus.OK,
        {"ok": True, "device": "classic", "settings": _classic_settings_api_payload(settings)},
    )


def _control_epever_charge_settings(supervisor: Supervisor, payload: dict) -> DisplayResponse:
    try:
        voltage_kwargs = {
            "boost_v": _first_optional_number(payload, "absorb_voltage_v", "boost_voltage_v"),
            "float_v": _optional_number(payload, "float_voltage_v"),
            "equalize_v": _optional_number(payload, "equalize_voltage_v"),
            "boost_reconnect_v": _first_optional_number(
                payload,
                "boost_reconnect_voltage_v",
                "bulk_recovery_voltage_v",
            ),
        }
        voltage_kwargs = {key: value for key, value in voltage_kwargs.items() if value is not None}
        current_a = _optional_number(payload, "max_charging_current_a")
        time_kwargs = {
            "boost_time_minutes": _optional_int(payload, "absorb_time_minutes"),
            "equalize_time_minutes": _optional_int(payload, "equalize_time_minutes"),
        }
        time_kwargs = {key: value for key, value in time_kwargs.items() if value is not None}
        if not voltage_kwargs and current_a is None and not time_kwargs:
            return _json_response(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "no EPEver charge settings supplied"},
            )
        settings = None
        if voltage_kwargs:
            snapshot = supervisor.read_snapshot()
            if snapshot.epever_settings is None:
                raise RuntimeError("EPEver settings unavailable; cannot guard voltage write")
            final_voltages = {
                "boost_voltage_v": voltage_kwargs.get("boost_v", snapshot.epever_settings.boost_voltage_v),
                "float_voltage_v": voltage_kwargs.get("float_v", snapshot.epever_settings.float_voltage_v),
                "equalize_voltage_v": voltage_kwargs.get("equalize_v", snapshot.epever_settings.equalize_voltage_v),
            }
            _guard_voltage_targets_against_bms(snapshot, final_voltages)
            settings = supervisor.write_epever_charge_voltages(**voltage_kwargs)
        if current_a is not None:
            settings = supervisor.write_epever_max_charging_current(current_a)
        if time_kwargs:
            settings = supervisor.write_epever_charge_times(**time_kwargs)
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    return _json_response(
        HTTPStatus.OK,
        {"ok": True, "device": "epever", "settings": _epever_settings_api_payload(settings)},
    )


def _control_epever_sync_from_classic(supervisor: Supervisor, payload: dict) -> DisplayResponse:
    try:
        voltage_offset_v = _optional_number(payload, "voltage_offset_v") or 0.0
        no_current = bool(payload.get("no_current", False))
        snapshot = supervisor.read_snapshot()
        if snapshot.classic_settings is None:
            raise RuntimeError("Classic settings unavailable")
        if snapshot.epever_settings is None:
            raise RuntimeError("EPEver settings unavailable")

        classic = snapshot.classic_settings
        target_boost = round(classic.absorb_voltage_v + voltage_offset_v, 2)
        target_float = round(classic.float_voltage_v + voltage_offset_v, 2)
        target_equalize = round(max(classic.equalize_voltage_v + voltage_offset_v, target_boost), 2)
        final_voltages = {
            "boost_voltage_v": target_boost,
            "float_voltage_v": target_float,
            "equalize_voltage_v": target_equalize,
        }
        _guard_voltage_targets_against_bms(snapshot, final_voltages)

        voltage_kwargs = {
            "boost_v": target_boost,
            "float_v": target_float,
            "equalize_v": target_equalize,
        }
        settings = supervisor.write_epever_charge_voltages(**voltage_kwargs)
        if not no_current:
            settings = supervisor.write_epever_max_charging_current(classic.battery_current_limit_a)
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "device": "epever", "error": str(exc)})
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "device": "epever", "error": str(exc)})
    return _json_response(
        HTTPStatus.OK,
        {
            "ok": True,
            "device": "epever",
            "voltage_offset_v": voltage_offset_v,
            "planned": final_voltages,
            "settings": _epever_settings_api_payload(settings),
        },
    )


def _control_epever_charging(supervisor: Supervisor, payload: dict) -> DisplayResponse:
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return _json_response(
            HTTPStatus.BAD_REQUEST,
            {"ok": False, "error": "enabled must be true or false"},
        )
    try:
        state = supervisor.set_epever_charging(enabled)
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)})
    return _json_response(HTTPStatus.OK, {"ok": True, "device": "epever", "enabled": state})


def _control_magnum_charge_settings(supervisor: Supervisor, payload: dict) -> DisplayResponse:
    try:
        voltage_targets = {
            "absorb_voltage_v": _optional_number(payload, "absorb_voltage_v"),
            "float_voltage_v": _optional_number(payload, "float_voltage_v"),
        }
        voltage_targets = {key: value for key, value in voltage_targets.items() if value is not None}
        if voltage_targets:
            _guard_voltage_targets_against_bms(supervisor.read_snapshot(), voltage_targets)
        supervisor.write_magnum_charge_settings(
            absorb_voltage_v=voltage_targets.get("absorb_voltage_v"),
            float_voltage_v=voltage_targets.get("float_voltage_v"),
            absorb_time_hr=_optional_number(payload, "absorb_time_hr"),
            charger_amps_pct=_optional_int(payload, "charger_amps_pct"),
            shore_amps=_optional_int(payload, "shore_amps"),
        )
    except ValueError as exc:
        return _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "device": "magnum", "error": str(exc)})
    except NotImplementedError as exc:
        return _json_response(
            HTTPStatus.NOT_IMPLEMENTED,
            {"ok": False, "device": "magnum", "error": str(exc), "reason": "not_implemented"},
        )
    except RuntimeError as exc:
        return _json_response(HTTPStatus.CONFLICT, {"ok": False, "device": "magnum", "error": str(exc)})
    return _json_response(HTTPStatus.OK, {"ok": True, "device": "magnum"})


def _guard_voltage_targets_against_bms(snapshot: SupervisorSnapshot, targets: dict[str, float]) -> None:
    if not targets:
        return
    if snapshot.battery is None or snapshot.battery.charge_limits is None:
        raise RuntimeError("BMS charge-voltage limit unavailable; refusing voltage write")
    limit = snapshot.battery.charge_limits.charge_voltage_limit_v
    exceeded = [f"{label} {value:.2f}V" for label, value in targets.items() if value > limit]
    if exceeded:
        raise ValueError(f"requested charge voltage exceeds BMS CVL: {', '.join(exceeded)} > {limit:.2f}V")


def _controller_number(payload: dict) -> int:
    for key in ("controller", "controller_number", "charge_controller_number"):
        if key in payload and payload.get(key) is not None:
            value = payload.get(key)
            if isinstance(value, bool):
                raise ValueError(f"{key} must be a charge controller number")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a charge controller number") from exc
    raise ValueError("controller must be supplied")


def _required_number(payload: dict, key: str) -> float:
    value = _optional_number(payload, key)
    if value is None:
        raise ValueError(f"{key} must be supplied")
    return value


def _optional_number(payload: dict, key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc


def _first_optional_number(payload: dict, *keys: str) -> float | None:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return _optional_number(payload, key)
    return None


def _optional_int(payload: dict, key: str) -> int | None:
    value = _optional_number(payload, key)
    return None if value is None else int(value)


def _classic_settings_api_payload(settings) -> dict | None:
    if settings is None:
        return None
    return {
        "current_limit_a": settings.battery_current_limit_a,
        "absorb_voltage_v": settings.absorb_voltage_v,
        "float_voltage_v": settings.float_voltage_v,
        "equalize_voltage_v": settings.equalize_voltage_v,
        "absorb_time_minutes": settings.absorb_time_s / 60,
        "max_temp_comp_voltage_v": settings.max_temp_comp_voltage_v,
        "min_temp_comp_voltage_v": settings.min_temp_comp_voltage_v,
        "temp_comp_mv_per_c_cell": settings.temp_comp_mv_per_c_cell,
    }


def _epever_settings_api_payload(settings) -> dict | None:
    if settings is None:
        return None
    return {
        "battery_type": settings.battery_type,
        "battery_type_code": settings.battery_type_code,
        "charging_limit_voltage_v": settings.charging_limit_voltage_v,
        "equalize_voltage_v": settings.equalize_voltage_v,
        "absorb_voltage_v": settings.boost_voltage_v,
        "boost_voltage_v": settings.boost_voltage_v,
        "float_voltage_v": settings.float_voltage_v,
        "boost_reconnect_voltage_v": settings.boost_reconnect_voltage_v,
        "bulk_recovery_voltage_v": settings.boost_reconnect_voltage_v,
        "max_charging_current_a": settings.max_charging_current_a,
        "absorb_time_minutes": settings.boost_time_minutes,
        "equalize_time_minutes": settings.equalize_time_minutes,
    }


def _hourly_weather_section(hourly: list) -> list[str]:
    if not hourly:
        return []
    rows = ["<h2>Next Hours</h2>", "<table>"]
    for hour in hourly[:8]:
        rows.append(
            _weather_row(
                _short_time(hour.get("at")),
                "  ".join(
                    item
                    for item in [
                        (hour.get("condition") or {}).get("text"),
                        _format_number(hour.get("temperature_c"), "C", decimals=1),
                        _format_number(hour.get("precip_probability_pct"), "% precip", decimals=0),
                        _format_number(hour.get("wind_speed_kmh"), "km/h", decimals=0),
                    ]
                    if item
                ),
            )
        )
    rows.append("</table>")
    return rows


def _daily_weather_section(daily: list) -> list[str]:
    if not daily:
        return []
    rows = ["<h2>Forecast</h2>", "<table>"]
    for day in daily[:3]:
        rows.append(
            _weather_row(
                _short_day(day.get("date")),
                "  ".join(
                    item
                    for item in [
                        (day.get("condition") or {}).get("text"),
                        _daily_temperature_text(day.get("low_c"), day.get("high_c")),
                        _format_number(day.get("precip_probability_pct"), "% precip", decimals=0),
                        _format_number(day.get("precip_sum_mm"), "mm", decimals=1),
                    ]
                    if item
                ),
            )
        )
    rows.append("</table>")
    return rows


def _solar_irradiance_section(irradiance: dict) -> list[str]:
    return [
        "<h2>Solar Irradiance</h2>",
        "<table>",
        _weather_row("Global Horizontal (GHI)", _format_number(irradiance.get("ghi_wm2"), "W/m2", decimals=0)),
        _weather_row("Direct Radiation", _format_number(irradiance.get("direct_wm2"), "W/m2", decimals=0)),
        _weather_row("Diffuse Radiation", _format_number(irradiance.get("diffuse_wm2"), "W/m2", decimals=0)),
        _weather_row("Direct Normal (DNI)", _format_number(irradiance.get("dni_wm2"), "W/m2", decimals=0)),
        "</table>",
    ]


def _astronomy_weather_section(astronomy: dict) -> list[str]:
    moon = astronomy.get("moon") or {}
    rows = ["<h2>Astronomy</h2>", "<table>"]
    rows.append(_weather_row("Sun", _sun_text(astronomy.get("sunrise"), astronomy.get("sunset"))))
    rows.append(_weather_row("Moon", _moon_text(moon)))
    rows.append(_weather_row_html("Aurora", _aurora_html(astronomy.get("aurora"))))
    rows.append("</table>")
    return rows


def _weather_row(label: str, value: str | None) -> str:
    return f"<tr><td>{escape(label)}</td><td>{escape(value or '--')}</td></tr>"


def _weather_row_html(label: str, value: str | None) -> str:
    return f"<tr><td>{escape(label)}</td><td>{value or '--'}</td></tr>"


def _format_number(value: object, suffix: str, decimals: int = 1) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return f"{number:.{decimals}f}{suffix}"


def _wind_text(wind: dict) -> str | None:
    speed_text = _format_number(wind.get("speed_kmh"), "km/h", decimals=0)
    if speed_text is None:
        return None
    gust_text = _format_number(wind.get("gust_kmh"), "km/h gust", decimals=0)
    parts = [speed_text]
    if gust_text:
        parts.append(gust_text)
    if wind.get("compass"):
        parts.append(wind["compass"])
    return "  ".join(parts)


def _precip_text(current: dict) -> str | None:
    parts = []
    for value, suffix in [
        (current.get("precipitation_mm"), "mm"),
        (current.get("rain_mm"), "mm rain"),
        (current.get("snowfall_cm"), "cm snow"),
    ]:
        text = _format_number(value, suffix, decimals=1)
        if text:
            parts.append(text)
    return "  ".join(parts) if parts else None


def _moon_text(moon: dict) -> str | None:
    name = moon.get("name")
    if not name:
        return None
    phase = moon.get("phase")
    return f"{name} ({phase:.2f})" if isinstance(phase, (int, float)) else name


def _sun_text(sunrise: object, sunset: object) -> str | None:
    sunrise_text = _short_time(sunrise)
    sunset_text = _short_time(sunset)
    if sunrise_text == "--" and sunset_text == "--":
        return None
    return f"rise {sunrise_text}  set {sunset_text}"


def _aurora_html(aurora: object) -> str | None:
    if not isinstance(aurora, dict):
        return None
    probability = _format_number(aurora.get("probability_pct"), "%", decimals=0)
    if probability is None:
        return None
    valid_at = aurora.get("valid_at")
    now_line = f"now {escape(probability)}"
    if isinstance(valid_at, str):
        now_line = f"{now_line} valid {escape(_short_time(valid_at))}"
    return f"{now_line}<br>{_aurora_tonight_text(aurora.get('tonight'))}"


def _aurora_tonight_text(tonight: object) -> str:
    if not isinstance(tonight, dict):
        return "tonight unavailable"
    kp = _format_number(tonight.get("peak_kp"), "", decimals=1)
    likelihood = tonight.get("likelihood")
    if kp is None or not isinstance(likelihood, str):
        return "tonight unavailable"
    peak_at = tonight.get("peak_at")
    time_text = _short_time(peak_at) if isinstance(peak_at, str) else "--"
    scale = tonight.get("scale")
    scale_text = f" {escape(str(scale))}" if scale else ""
    return f"tonight {escape(likelihood)} peak Kp {escape(kp)}{scale_text} at {escape(time_text)}"


def _daily_temperature_text(low: object, high: object) -> str | None:
    low_text = _format_number(low, "C", decimals=1)
    high_text = _format_number(high, "C", decimals=1)
    if low_text and high_text:
        return f"{low_text}-{high_text}"
    return high_text or low_text


def _parse_payload_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _short_time(value: object) -> str:
    if not isinstance(value, str):
        return "--"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%H:%M")


def _short_day(value: object) -> str:
    if not isinstance(value, str):
        return "--"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%a %m/%d")


# Per-device components reported in /api/v1/health "checks". Each entry maps a
# device's telemetry on the snapshot to its read-error prefix so a consumer can
# see *which* device is degraded, not just the overall verdict. Ambient has no
# error prefix — a failed probe simply means "no reading".
_HEALTH_COMPONENTS: tuple[tuple[str, str | None, Callable[[SupervisorSnapshot], object]], ...] = (
    ("classic", "Classic read failed", lambda s: s.classic),
    ("epever", "EPEver read failed", lambda s: s.epever),
    ("battery", "Battery CAN read failed", lambda s: s.battery),
    ("magnum", "Magnum read failed", lambda s: s.magnum),
    ("ambient", None, lambda s: s.ambient),
)


def _offline_reason(status: str, detail: str | None) -> str | None:
    # Classify only from the observed failure signature — never an inferred
    # root cause. "transport_absent" = the serial port/adapter is not present;
    # "no_response" = the port opened but the remote device stayed silent.
    if status == "ok":
        return None
    if status == "disabled":
        return "disabled"
    if status == "offline":
        return "no_data"
    text = (detail or "").lower()
    if "could not open" in text or "no such file or directory" in text or "no such device" in text:
        return "transport_absent"
    if "no response" in text or "timeout" in text or "timed out" in text:
        return "no_response"
    return "unknown"


def _health_checks(snapshot: SupervisorSnapshot) -> dict:
    checks: dict = {}
    for name, error_prefix, getter in _HEALTH_COMPONENTS:
        detail = None
        if error_prefix is not None:
            detail = next((msg for msg in snapshot.errors if msg.startswith(error_prefix)), None)
        if detail is not None:
            status = "error"
        elif name in snapshot.disabled_devices:
            status = "disabled"
        elif getter(snapshot) is None:
            status = "offline"
        else:
            status = "ok"
        checks[name] = {"status": status, "reason": _offline_reason(status, detail), "detail": detail}
    return checks


def health_api_payload(snapshot: SupervisorSnapshot, now: datetime | None = None) -> dict:
    # schema_version 2: "status" now distinguishes WARNING (degraded, HTTP 200)
    # from ERROR (HTTP 503), and per-device "checks" + "conditions" are added.
    return {
        "schema_version": 2,
        "ok": snapshot.ok,
        "status": snapshot.status_text,
        "captured_at": snapshot.captured_at.isoformat(),
        "age_seconds": _age_seconds(snapshot.captured_at, now=now),
        "errors": list(snapshot.errors),
        "conditions": list(snapshot.status_conditions),
        "checks": _health_checks(snapshot),
    }


def snapshot_api_payload(
    snapshot: SupervisorSnapshot,
    load_summary: LoadSummary | None = None,
    now: datetime | None = None,
    site_id: str = "cabin",
    allocation: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "site_id": site_id,
        "captured_at": snapshot.captured_at.isoformat(),
        "age_seconds": _age_seconds(snapshot.captured_at, now=now),
        "status": {
            "ok": snapshot.ok,
            "severity": snapshot.status_text,
            "annotations": snapshot_status_annotations(snapshot),
            "errors": list(snapshot.errors),
            "conditions": list(snapshot.status_conditions),
        },
        "battery": _battery_api_payload(snapshot),
        "solar": _solar_api_payload(snapshot),
        "inverter": _inverter_api_payload(snapshot),
        "load": _load_api_payload(load_summary),
        "allocation": allocation,
        "ambient": _ambient_api_payload(snapshot),
        "reader_error_rates": snapshot.reader_error_rates,
        "lan_reachable": snapshot.lan_reachable,
        "wan_reachable": snapshot.wan_reachable,
    }


def _json_response(status: HTTPStatus, payload: dict) -> DisplayResponse:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    return DisplayResponse(status, "application/json; charset=utf-8", body)


def _age_seconds(captured_at: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(captured_at.tzinfo)
    return max(0, int((now - captured_at).total_seconds()))


def _battery_api_payload(snapshot: SupervisorSnapshot) -> dict | None:
    battery = snapshot.battery
    if battery is None:
        return None

    state = battery.state_of_charge
    measurements = battery.measurements
    extended = battery.extended_measurements
    limits = battery.charge_limits
    flags = battery.request_flags
    status = battery.status
    payload: dict = {
        "soc_percent": state.soc_percent if state is not None else None,
        "soh_percent": state.soh_percent if state is not None else None,
        "voltage_v": measurements.voltage_v if measurements is not None else None,
        "current_a": measurements.current_a if measurements is not None else None,
        "power_w": (measurements.voltage_v * measurements.current_a) if measurements is not None else None,
        "temperature_c": measurements.temperature_c if measurements is not None else None,
        "cell_min_v": extended.min_cell_voltage_v if extended is not None else None,
        "cell_max_v": extended.max_cell_voltage_v if extended is not None else None,
        "cell_delta_mv": None,
        "cell_min_pack_number": extended.min_cell_pack_number if extended is not None else None,
        "cell_min_number": extended.min_cell_number if extended is not None else None,
        "cell_min_location": extended.min_cell_location_text() if extended is not None else None,
        "cell_max_pack_number": extended.max_cell_pack_number if extended is not None else None,
        "cell_max_number": extended.max_cell_number if extended is not None else None,
        "cell_max_location": extended.max_cell_location_text() if extended is not None else None,
        "cell_temperature_min_c": extended.min_cell_temperature_c if extended is not None else None,
        "cell_temperature_max_c": extended.max_cell_temperature_c if extended is not None else None,
        "installed_capacity_ah": extended.installed_capacity_ah if extended is not None else None,
        "charge_enabled": flags.charge_enable if flags is not None else None,
        "discharge_enabled": flags.discharge_enable if flags is not None else None,
        "force_charge_1": flags.force_charge_1 if flags is not None else None,
        "force_charge_2": flags.force_charge_2 if flags is not None else None,
        "full_charge_request": flags.full_charge_request if flags is not None else None,
        "charge_voltage_limit_v": limits.charge_voltage_limit_v if limits is not None else None,
        "charge_current_limit_a": limits.charge_current_limit_a if limits is not None else None,
        "discharge_current_limit_a": limits.discharge_current_limit_a if limits is not None else None,
        "discharge_voltage_limit_v": limits.discharge_voltage_limit_v if limits is not None else None,
        "module_count": status.module_count if status is not None else None,
        "protection_flags": list(status.protection_flags) if status is not None else [],
        "alarm_flags": list(status.alarm_flags) if status is not None else [],
        "manufacturer": battery.manufacturer,
        "manufacturer_marker": status.manufacturer_marker if status is not None else None,
    }
    if extended is not None and extended.min_cell_voltage_v is not None and extended.max_cell_voltage_v is not None:
        payload["cell_delta_mv"] = round((extended.max_cell_voltage_v - extended.min_cell_voltage_v) * 1000)
    return payload


def _solar_api_payload(snapshot: SupervisorSnapshot) -> list[dict]:
    payload: list[dict] = []
    if snapshot.classic is not None:
        classic = snapshot.classic

        settings = snapshot.classic_settings
        conditions: list[str] = []
        if classic.is_hypervoc:
            conditions.append(
                f"HyperVOC protection  Last Voc {classic.last_voc_v:.1f}V  High {classic.highest_input_voltage_v:.1f}V"
            )
        payload.append(
            {
            "id": "classic.0",
            # Vendor/model identity lives in its own block so renderers display
            # any controller generically and never branch on which one it is.
            "device": {"vendor": "MidNite", "model": "Classic 200", "short_name": "Classic"},
            "conditions": conditions,
            "captured_at": classic.captured_at.isoformat(),
            "battery_voltage_v": classic.battery_voltage_v,
            "battery_current_a": classic.battery_current_a,
            "battery_power_w": classic.battery_power_w,
            "pv_voltage_v": classic.pv_voltage_v,
            "pv_current_a": classic.pv_current_a,
            "daily_energy_kwh": classic.daily_energy_kwh,
            "daily_amp_hours_ah": classic.daily_amp_hours_ah,
            "lifetime_energy_kwh": classic.lifetime_energy_kwh,
            "lifetime_amp_hours_ah": classic.lifetime_amp_hours_ah,
            "last_voc_v": classic.last_voc_v,
            "highest_input_voltage_v": classic.highest_input_voltage_v,
            "charge_stage_code": classic.charge_stage_code,
            "charge_stage": classic.stage.as_dict(),
            "state_code": classic.state_code,
            "state": classic.state,
            "info_flags": classic.info_flags,
            "active_flags": list(classic.active_flags),
            "protection_enabled": {
                "ground_fault": classic.ground_fault_protection_enabled,
                "arc_fault": classic.arc_fault_protection_enabled,
            },
            "protection_text": _protection_text(
                classic.ground_fault_protection_enabled,
                classic.arc_fault_protection_enabled,
            ),
            "temperatures_c": {
                "battery": classic.battery_temp_c,
                "fet": classic.fet_temp_c,
                "pcb": classic.pcb_temp_c,
            },
            "settings": None
            if settings is None
            else {
                "current_limit_a": settings.battery_current_limit_a,
                "absorb_voltage_v": settings.absorb_voltage_v,
                "float_voltage_v": settings.float_voltage_v,
                "equalize_voltage_v": settings.equalize_voltage_v,
                "absorb_time_minutes": settings.absorb_time_s / 60,
                "max_temp_comp_voltage_v": settings.max_temp_comp_voltage_v,
            },
        }
        )
    elif "classic" not in snapshot.disabled_devices:
        payload.append({
            "id": "classic.0",
            "device": {"vendor": "MidNite", "model": "Classic 200", "short_name": "Classic"},
            "status": "unreachable",
        })
    if snapshot.epever is not None:
        epever = snapshot.epever
        settings = snapshot.epever_settings
        payload.append(
            {
                "id": "epever.1",
                "device": {"vendor": "EPEver", "model": "TEP10425", "short_name": "Epever"},
                "conditions": [],
                "captured_at": epever.captured_at.isoformat(),
                "battery_voltage_v": epever.battery_voltage_v,
                "battery_current_a": epever.battery_current_a,
                "battery_power_w": epever.battery_power_w,
                "pv_voltage_v": epever.pv_voltage_v,
                "pv_current_a": epever.pv_current_a,
                "pv_power_w": epever.pv_power_w,
                "battery_soc_percent": epever.battery_soc_percent,
                "charge_stage": epever.stage.as_dict(),
                "state": None,
                "status_raw": epever.status_raw,
                # Expose the EPEver's daily generation under the shared
                # daily_energy_kwh key so the vendor-agnostic renderers show it
                # as "Production Today", structured like the Classic group.
                # (The rated PV/charge figures are static, so we drop them.)
                "daily_energy_kwh": epever.generated_today_kwh,
                "daily_energy_unavailable_reason": epever.generated_today_unavailable_reason,
                "generated_total_kwh": epever.generated_total_kwh,
                "temperatures_c": {
                    "battery": epever.battery_temp_c,
                    "pcb": epever.pcb_temp_c,
                },
                "settings": None
                if settings is None
                else {
                    "battery_type": settings.battery_type,
                    "battery_type_code": settings.battery_type_code,
                    "battery_capacity_ah": settings.battery_capacity_ah,
                    "charging_limit_voltage_v": settings.charging_limit_voltage_v,
                    "boost_voltage_v": settings.boost_voltage_v,
                    "absorb_voltage_v": settings.boost_voltage_v,
                    "float_voltage_v": settings.float_voltage_v,
                    "equalize_voltage_v": settings.equalize_voltage_v,
                    "boost_reconnect_voltage_v": settings.boost_reconnect_voltage_v,
                    "bulk_recovery_voltage_v": settings.boost_reconnect_voltage_v,
                    "absorb_time_minutes": settings.boost_time_minutes,
                    "equalize_time_minutes": settings.equalize_time_minutes,
                    "max_charging_current_a": settings.max_charging_current_a,
                    "low_voltage_disconnect_v": settings.low_voltage_disconnect_v,
                    "discharging_limit_voltage_v": settings.discharging_limit_voltage_v,
                },
            }
        )
    elif "epever" not in snapshot.disabled_devices:
        payload.append({
            "id": "epever.1",
            "device": {"vendor": "EPEver", "model": "TEP10425", "short_name": "Epever"},
            "status": "unreachable",
        })
    return payload


def _inverter_api_payload(snapshot: SupervisorSnapshot) -> dict | None:
    inv = snapshot.magnum
    if inv is None:
        return None
    return {
        "captured_at": inv.captured_at.isoformat(),
        "dc_volts": inv.dc_volts,
        "dc_amps": inv.dc_amps,
        "dc_power_w": inv.dc_power_w,
        "ac_volts_out": inv.ac_volts_out,
        "ac_amps_out": inv.ac_amps_out,
        "ac_freq_hz": inv.ac_freq_hz,
        "ac_volts_in": inv.ac_volts_in,
        "ac_amps_in": inv.ac_amps_in,
        "inverter_on": inv.inverter_on,
        "charger_on": inv.charger_on,
        "status": inv.status_name,
        "status_label": inv.status_label(),
        "fault": inv.fault_name,
        "battery_temp_c": inv.battery_temp_c,
        "transformer_temp_c": inv.transformer_temp_c,
        "fet_temp_c": inv.fet_temp_c,
        "settings": {
            "absorb_v": inv.absorb_v,
            "float_v": inv.float_v,
            "absorb_time_hr": inv.absorb_time_hr,
            "shore_amps": inv.shore_amps,
            "charger_amps_pct": inv.charger_amps_pct,
        },
    }


def _load_api_payload(load_summary: LoadSummary | None) -> dict | None:
    if load_summary is None:
        return None
    return {
        "current_a": load_summary.current_a,
        "power_w": load_summary.power_w,
        "average_today_text": load_summary.average_today_text,
        "today_text": load_summary.today_text,
        "remaining_text": load_summary.remaining_text,
        "rolling_average_a": load_summary.rolling_average_a,
        "rolling_average_w": load_summary.rolling_average_w,
        "estimated_autonomy_hours": _hours_text_value(load_summary.remaining_text),
    }


def _hours_text_value(text: str | None) -> float | None:
    if text is None or not text.endswith("h"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


def _ambient_api_payload(snapshot: SupervisorSnapshot) -> dict | None:
    if snapshot.ambient is None:
        return None
    return {
        "captured_at": snapshot.ambient.captured_at.isoformat(),
        "temperature_c": snapshot.ambient.temperature_c,
        "humidity_percent": snapshot.ambient.humidity_percent,
    }


def run_display_server(
    supervisor: Supervisor,
    host: str = "0.0.0.0",
    port: int = 8080,
    snapshot_provider: Callable[[], SupervisorSnapshot] | None = None,
    load_summary_provider: Callable[[], LoadSummary | None] | None = None,
    weather_provider: Callable[[], WeatherReport | None] | None = None,
    refresh_hook: Callable[[], None] | None = None,
    weather_refresh_hook: Callable[[], None] | None = None,
    allocation_provider: Callable[[], dict | None] | None = None,
    charge_ceiling=None,
    allocation_override=None,
    relay_controller=None,
) -> None:
    provider = snapshot_provider or supervisor.read_snapshot
    refresh = refresh_hook or supervisor.request_refresh
    weather_refresh = weather_refresh_hook
    load_tracker = LoadTracker(sample_buffer=LoadSampleBuffer())

    class Handler(BaseHTTPRequestHandler):
        server_version = "OffGridPowerDisplay/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            try:
                snapshot = provider()
            except Exception as exc:  # noqa: BLE001 - HTTP display should show readiness errors.
                if urlparse(self.path).path.startswith("/api/"):
                    body = json.dumps(
                        {
                            "schema_version": 1,
                            "ok": False,
                            "status": "UNAVAILABLE",
                            "error": str(exc),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8") + b"\n"
                    self.send_response(HTTPStatus.SERVICE_UNAVAILABLE.value)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = render_snapshot_unavailable(exc).encode("utf-8")
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE.value)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if urlparse(self.path).path == "/healthz":
                load_summary = None
                weather_report = None
            elif load_summary_provider is not None:
                load_summary = load_summary_provider()
                weather_report = weather_provider() if weather_provider is not None and urlparse(self.path).path in _WEATHER_VIEW_PATHS else None
            else:
                load_summary = load_tracker.update(snapshot)
                weather_report = weather_provider() if weather_provider is not None and urlparse(self.path).path in _WEATHER_VIEW_PATHS else None
            allocation_path = urlparse(self.path).path
            if allocation_path == "/api/v1/control/allocation/status":
                response = _json_response(
                    HTTPStatus.OK,
                    allocation_override.status() if allocation_override is not None else {"paused": False, "manual_limits_a": {}},
                )
                self._send_display_response(response)
                return
            if allocation_path == "/api/v1/relay/state":
                if relay_controller is None:
                    response = _json_response(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": "relay controller not configured"})
                else:
                    response = _json_response(HTTPStatus.OK, {"ok": True, "stub": relay_controller.is_stub, **relay_controller.state()})
                self._send_display_response(response)
                return
            allocation = (
                allocation_provider()
                if allocation_provider is not None
                and allocation_path in {"/", "/kindle", "/kindle/details", "/display", "/api/v1/snapshot"}
                else None
            )
            response = route_display_request(
                snapshot,
                self.path,
                self.headers.get("User-Agent", ""),
                load_summary=load_summary,
                weather_report=weather_report,
                refresh_hook=refresh,
                weather_refresh_hook=weather_refresh,
                allocation=allocation,
            )
            self.send_response(response.status.value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            parsed_path = urlparse(self.path).path
            if not parsed_path.startswith("/api/v1/control/"):
                response = _json_response(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                self._send_display_response(response)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            try:
                body = self.rfile.read(content_length) if content_length > 0 else b"{}"
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                response = _json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                self._send_display_response(response)
                return
            response = route_control_request(supervisor, parsed_path, payload, charge_ceiling=charge_ceiling, allocation_override=allocation_override, relay_controller=relay_controller)
            self._send_display_response(response)

        def _send_display_response(self, response: DisplayResponse) -> None:
            self.send_response(response.status.value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib name
            return

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


def _kindle_nav_hint(label: str, align: str) -> list[str]:
    # Plaintext hint after the last content (not a button). It points the user to
    # the invisible page-turn tap zone on that side of the screen; we don't try to
    # pin it to the screen bottom because this browser can't do that reliably.
    return [f'<div class="nav-hint" style="text-align:{align};">{escape(label)}</div>']


def _page_turn_link(href: str, side: str, label: str) -> str:
    return f'<a class="page-turn page-turn-{escape(side)}" href="{escape(href)}">{escape(label)}</a>'


def _status_summary_section(snapshot: SupervisorSnapshot) -> list[str]:
    # One off-normal group "Warnings and Faults": read failures (errors) and
    # analyzed conditions / BMS faults all mean "something is wrong", so they
    # share a group (saves vertical space; no contradictory error-next-to-"none").
    # Errors first (more urgent), then conditions.
    messages = list(snapshot.errors) + list(snapshot.status_conditions)
    # Kindle-only group: when nothing is wrong, omit it entirely rather than
    # show a standalone "none" -- saves a row on the cramped e-ink, and a
    # permanently-present empty group is just more burn-in to ghost.
    if not messages:
        return []
    value = "; ".join(messages)
    return ["<h2>Warnings and Faults</h2>", "<table>", _full_row(value), "</table>"]


def _charge_controller_sections(
    snapshot: SupervisorSnapshot,
    allocation: dict | None = None,
    *,
    include_live: bool = True,
    include_settings: bool = True,
) -> list[str]:
    # Iterate the normalized controller collection: the renderer carries no
    # knowledge of which vendors or how many controllers exist. A new model
    # appears as its own group automatically once the API reports it.
    controllers = _solar_api_payload(snapshot)
    if not controllers:
        return ["<h2>Charge Controllers</h2>", "<table>", _row("State", "No data"), "</table>"]
    lines: list[str] = []
    for index, controller in enumerate(controllers):
        lines.extend(
            _controller_section_lines(
                index,
                controller,
                allocation_target=_allocation_target(controller, allocation),
                include_live=include_live,
                include_settings=include_settings,
            )
        )
    return lines


def _allocation_target(controller: dict, allocation: dict | None) -> dict | None:
    targets = (allocation or {}).get("targets") or {}
    controller_id = controller.get("id")
    if isinstance(controller_id, str):
        target = targets.get(controller_id.split(".", 1)[0])
        if isinstance(target, dict):
            return target
    return None


def _controller_section_lines(
    index: int,
    controller: dict,
    allocation_target: dict | None = None,
    *,
    include_live: bool = True,
    include_settings: bool = True,
) -> list[str]:
    name = _charge_controller_short_name(controller)
    title = f"Charge Controller {index} ({name})" if name else f"Charge Controller {index}"
    if controller.get("status") == "unreachable":
        return [f"<h2>{escape(title)} — UNREACHABLE</h2>"]
    lines = [f"<h2>{escape(title)}</h2>", "<table>"]

    if include_live:
        for condition in controller.get("conditions") or []:
            lines.append(_row("Alert", condition))

        pv_parts = [_meas(controller.get("pv_voltage_v"), "V", 1), _meas(controller.get("pv_current_a"), "A", 1)]
        if controller.get("last_voc_v") is not None:
            pv_parts.append(f"Voc {_meas(controller.get('last_voc_v'), 'V', 1)}")
        elif controller.get("pv_power_w") is not None:
            pv_parts.append(_meas(controller.get("pv_power_w"), "W", 0))
        lines.append(_row("PV", "  ".join(pv_parts)))

        lines.append(
            _row(
                "Output",
                f"{_meas(controller.get('battery_voltage_v'), 'V', 1)}  "
                f"{_meas(controller.get('battery_current_a'), 'A', 1)}  "
                f"{_meas(controller.get('battery_power_w'), 'W', 0)}",
            )
        )

        stage = NormalizedStage.from_dict(controller.get("charge_stage"))
        lines.append(_row("Charge Status", stage.render(controller.get("state"))))

        allocation_text = _allocation_target_text(allocation_target)
        if allocation_text is not None:
            lines.append(_row("Allocation", allocation_text))

        if controller.get("daily_energy_kwh") is not None or controller.get("daily_amp_hours_ah") is not None:
            parts = []
            if controller.get("daily_energy_kwh") is not None:
                parts.append(_energy_text(controller.get("daily_energy_kwh")))
            if controller.get("daily_amp_hours_ah") is not None:
                parts.append(_meas(controller.get("daily_amp_hours_ah"), "Ah", 0))
            lines.append(_row("Production Today", "  ".join(parts)))

        if controller.get("rated_pv_voltage_v") is not None or controller.get("rated_charging_current_a") is not None:
            lines.append(
                _row(
                    "Rated",
                    f"{_meas(controller.get('rated_pv_voltage_v'), 'V', 0)} PV  "
                    f"{_meas(controller.get('rated_charging_current_a'), 'A', 0)} charge",
                )
            )

    settings_text = _charge_controller_settings_text(controller.get("settings"))
    if include_settings and settings_text is not None:
        lines.append(_row("Charge Settings", settings_text))

    lines.append("</table>")
    return lines


def _charge_controller_short_name(controller: dict) -> str:
    device = controller.get("device") or {}
    short_name = str(device.get("short_name") or "").strip()
    if short_name:
        return short_name
    return " ".join(str(part).strip() for part in [device.get("vendor"), device.get("model")] if part)


def _protection_text(ground_fault: bool | None, arc_fault: bool | None) -> str | None:
    """Compact GFP / arc-fault armed-state line for the displays.

    None when neither bit was read (e.g. EPEver, which has no such config).
    """
    if ground_fault is None and arc_fault is None:
        return None
    return f"GFP {'on' if ground_fault else 'off'}  Arc {'on' if arc_fault else 'off'}"


def _charge_controller_settings_text(settings: dict | None) -> str | None:
    if settings is None:
        return None
    if "current_limit_a" in settings:
        return (
            f"{_meas(settings.get('current_limit_a'), 'A', 1)} "
            f"Abs {_meas(settings.get('absorb_voltage_v'), 'V', 1)}/{_minutes_text(settings.get('absorb_time_minutes'))} "
            f"Flt {_meas(settings.get('float_voltage_v'), 'V', 1)} "
            f"TCV {_meas(settings.get('max_temp_comp_voltage_v'), 'V', 1)}"
        )
    return (
        f"{_meas(settings.get('max_charging_current_a'), 'A', 1)} "
        f"Abs {_meas(_first_present(settings, 'absorb_voltage_v', 'boost_voltage_v'), 'V', 1)}/{_minutes_text(settings.get('absorb_time_minutes'))} "
        f"Flt {_meas(settings.get('float_voltage_v'), 'V', 1)} "
        f"Rec {_meas(_first_present(settings, 'bulk_recovery_voltage_v', 'boost_reconnect_voltage_v'), 'V', 1)}"
    )


def _allocation_target_text(target: dict | None) -> str | None:
    if target is None:
        return None
    reason = target.get("reason")
    target_a = target.get("target_a")
    if target.get("disable"):
        text = "off"
    elif reason in {"unconstrained", "charger inactive", "charger unavailable"}:
        text = "released"
    elif target_a is None:
        text = "--"
    else:
        text = f"limited {_meas(target_a, 'A', 1)}"
    if target.get("should_write"):
        text += " *"
    return text


def _meas(value: object, suffix: str, decimals: int = 1) -> str:
    text = _format_number(value, suffix, decimals)
    return text if text is not None else "--"


def _energy_text(kwh) -> str:
    """Readable energy: Wh (integer) below 1 kWh, kWh (1 decimal) above. The
    counters are 10 Wh (EPEver) / 100 Wh (Classic) resolution, so the Wh value is
    quantized to those steps -- this just avoids the '0.0x kWh' clutter."""
    if kwh is None:
        return "--"
    wh = float(kwh) * 1000
    return f"{round(wh)} Wh" if abs(wh) < 1000 else f"{float(kwh):.1f} kWh"


def _first_present(mapping: dict, *keys: str):
    for key in keys:
        if mapping.get(key) is not None:
            return mapping.get(key)
    return None


def _minutes_text(minutes: object) -> str:
    try:
        return f"{float(minutes):g}m"
    except (TypeError, ValueError):
        return "--"


def _inverter_charger_section(snapshot: SupervisorSnapshot) -> list[str]:
    lines = ["<h2>Inverter/Charger</h2>", "<table>"]
    inv = snapshot.magnum
    if inv is None:
        lines.append(_row("State", "No data"))
        lines.append("</table>")
        return lines

    lines.append(_row("DC", f"{inv.dc_volts:.1f}V  {inv.dc_amps}A  {inv.dc_power_w}W"))

    ac_out_parts = [f"{inv.ac_volts_out}V"]
    if inv.ac_amps_out is not None:
        ac_out_parts.append(f"{inv.ac_amps_out}A")
    if inv.ac_freq_hz is not None:
        ac_out_parts.append(f"{inv.ac_freq_hz:.1f}Hz")
    lines.append(_row("AC Output", "  ".join(ac_out_parts)))

    if inv.ac_volts_in > 0:
        ac_in_parts = [f"{inv.ac_volts_in}V"]
        if inv.ac_amps_in is not None:
            ac_in_parts.append(f"{inv.ac_amps_in}A")
        lines.append(_row("AC Input", "  ".join(ac_in_parts)))
    else:
        lines.append(_row("AC Input", "0V  no source"))

    status_text = inv.status_label()
    fault = inv.fault_label()
    if fault:
        status_text += f"  Fault: {fault}"
    lines.append(_row("Status", status_text))

    settings_parts = []
    if inv.charger_amps_pct is not None and inv.charger_amps_pct > 0:
        settings_parts.append(f"Limit {inv.charger_amps_pct}%")
    if inv.absorb_v is not None:
        absorb = f"Absorb {inv.absorb_v:.1f}V"
        if inv.absorb_time_hr is not None:
            absorb += f" {inv.absorb_time_hr:.1f}h"
        settings_parts.append(absorb)
    if inv.float_v is not None:
        settings_parts.append(f"Float {inv.float_v:.1f}V")
    if inv.shore_amps is not None:
        settings_parts.append(f"Shore {inv.shore_amps}A")
    if settings_parts:
        lines.append(_row("Charge Settings", " ".join(settings_parts)))

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
        min_location = format_cell_location_for_display(extended.min_cell_location_text())
        max_location = format_cell_location_for_display(extended.max_cell_location_text())
        value = (
            f"Δ {delta_mv}mV; "
            f"min {min_location} {extended.min_cell_voltage_v:.3f}V; "
            f"max {max_location} {extended.max_cell_voltage_v:.3f}V"
        )
        lines.append(_row("Cells", value))
    # Protections/alarms surface in the Status Conditions group (off-normal
    # status), not as a passive battery row.
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
        lines.append(_row("CC0 FET", f"{classic.fet_temp_c:.1f}C"))
        lines.append(_row("CC0 PCB", f"{classic.pcb_temp_c:.1f}C"))
    if snapshot.epever is not None and snapshot.epever.pcb_temp_c is not None:
        lines.append(_row("CC1 PCB", f"{snapshot.epever.pcb_temp_c:.1f}C"))
    if snapshot.magnum is not None:
        inv = snapshot.magnum
        lines.append(_row("INV battery", f"{inv.battery_temp_c}C"))
        lines.append(_row("INV transformer", f"{inv.transformer_temp_c}C"))
        lines.append(_row("INV FET", f"{inv.fet_temp_c}C"))
    if snapshot.ambient is None:
        lines.append(_row("Sensor 0 ambient", "disconnected"))
    else:
        lines.append(_row("Sensor 0 ambient", f"{snapshot.ambient.temperature_c:.1f}C"))
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


def _full_row(value: str) -> str:
    return f'<tr><td colspan="2">{escape(value)}</td></tr>'


def _battery_state(current_a: float) -> str:
    if current_a > BATTERY_IDLE_CURRENT_A:
        return "charging"
    if current_a < -BATTERY_IDLE_CURRENT_A:
        return "discharging"
    return "idle"


def _status_text(snapshot: SupervisorSnapshot, status: str) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return f"Status: {status}"
    return f"SOC: {snapshot.battery.state_of_charge.soc_percent}%  Status: {status}"


def _soc_text(snapshot: SupervisorSnapshot) -> str:
    if snapshot.battery is None or snapshot.battery.state_of_charge is None:
        return "--"
    return f"{snapshot.battery.state_of_charge.soc_percent}%"


def _row_lines(label: str, values: list[str]) -> str:
    escaped_values = "<br>".join(escape(value) for value in values)
    return f"<tr><td>{escape(label)}</td><td>{escaped_values}</td></tr>"

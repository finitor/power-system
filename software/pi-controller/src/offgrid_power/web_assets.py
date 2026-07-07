"""Sidecar web assets (site icons) shared by every rendered page.

One module owns the icon bytes, their URL paths, and the <link> tags that
reference them, so a new page cannot ship without icons and an artwork or
path change happens in exactly one place. `ASSET_CONTENT` is the single
routing truth: both the display router and the HTTP server consult it.
"""

from __future__ import annotations

import base64
import struct

FAVICON_PNG_PATH = "/favicon.png"
FAVICON_ICO_PATH = "/favicon.ico"
FAVICON_SVG_PATH = "/favicon.svg"
APPLE_TOUCH_ICON_PATH = "/apple-touch-icon.png"
# Older iOS Safari probes this name unprompted; serve it rather than 404.
APPLE_TOUCH_ICON_PRECOMPOSED_PATH = "/apple-touch-icon-precomposed.png"

# Bump on artwork edits. Icon URLs carry ?v=FAVICON_VERSION, so a given URL's
# content never changes and browsers may cache it forever (ASSET_CACHE_CONTROL).
FAVICON_VERSION = "20260707-bolt-transparent"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"

FAVICON_PNG = base64.b64decode(
    "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"
)
FAVICON_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<circle cx="34" cy="35" r="20" fill="#4468d8"/>
<circle cx="29" cy="30" r="13" fill="#6587ff" opacity=".65"/>
<path d="M14 45c10 9 28 10 41-2-6 14-29 18-41 2z" fill="#24336c" opacity=".45"/>
<path d="M21 11c8 1 14 5 18 13-9 0-15-4-18-13z" fill="#68b154"/>
<path d="M38 12c-7 3-11 7-13 13 9 1 15-3 13-13z" fill="#92d369"/>
<circle cx="25" cy="27" r="4" fill="#e1e8ff" opacity=".8"/>
<path d="M39 13 25 34h9l-7 19 21-26H37z" fill="#171a27" opacity=".75"/>
<path d="M39 13 25 34h9l-7 19 21-26H37z" fill="#ffd350"/>
</svg>
"""
FAVICON_ICO = (
    struct.pack("<HHH", 0, 1, 1)
    + struct.pack("<BBBBHHII", 64, 64, 0, 0, 1, 32, len(FAVICON_PNG), 22)
    + FAVICON_PNG
)

# path -> (content type, body)
ASSET_CONTENT: dict[str, tuple[str, bytes]] = {
    FAVICON_ICO_PATH: ("image/x-icon", FAVICON_ICO),
    FAVICON_PNG_PATH: ("image/png", FAVICON_PNG),
    FAVICON_SVG_PATH: ("image/svg+xml", FAVICON_SVG),
    APPLE_TOUCH_ICON_PATH: ("image/png", FAVICON_PNG),
    APPLE_TOUCH_ICON_PRECOMPOSED_PATH: ("image/png", FAVICON_PNG),
}


def favicon_links() -> str:
    version = f"?v={FAVICON_VERSION}"
    return "\n".join(
        [
            f'<link rel="icon" href="{FAVICON_ICO_PATH}{version}" sizes="any">',
            f'<link rel="icon" href="{FAVICON_PNG_PATH}{version}" type="image/png" sizes="64x64">',
            f'<link rel="apple-touch-icon" href="{APPLE_TOUCH_ICON_PATH}{version}">',
            f'<link rel="icon" href="{FAVICON_SVG_PATH}{version}" type="image/svg+xml">',
        ]
    )

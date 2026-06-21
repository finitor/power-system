# E-ink Wall Display Upgrade Path

Exploratory note (not a decision yet) on moving the wall display off the
jailbroken **Kindle Touch (K5, 2011)** to something with a modern, compliant
browser. Motivated by the 2011-WebKit fight documented in
[journal/2026-06-21.md](../journal/2026-06-21.md): the device works, but every
layout and ghosting issue traces back to its frozen, crippled browser engine.

## The core constraint

The browser is the locked part, not the screen.

- **Kindle (any generation)** ships Amazon's deliberately limited "Experimental"
  WebKit across the whole line (Paperwhite, Voyage, Oasis, current models).
  Newer = faster, but the same stale, crippled engine — and newer firmware is
  *harder* to jailbreak. Upgrading within the Kindle family does **not** get a
  better browser.
- **Kobo** is the same class: Linux firmware, weak browser; jailbreaking leads to
  the image-push pattern (below), not a modern browser.

The dividing line is the OS:

- **Linux-firmware e-ink readers (Kindle, Kobo)** → locked, weak browser.
- **Android e-ink devices** → install a real, current browser.

## Two architectures

### A. Keep a browser → Android e-ink device (preferred; matches the goal)

An Android e-ink tablet runs a current Chrome/WebView, so the dashboard renders
with normal modern CSS. Pair it with **Fully Kiosk Browser** (purpose-built for
wall dashboards: kiosk lockdown, auto-refresh, screensaver, remote admin) and use
the device's OS-level **e-ink refresh modes** (full/Regal) to manage ghosting —
replacing our JS flash hack entirely.

Near drop-in: point it at the supervisor's existing HTML. Then delete the
Kindle-WebKit scaffolding in `web_display.py` (the `fullRefresh` flash,
padding-sized tap zones, the `vh`/`fixed`/`min-height` avoidance, grey-border
removal) and rebuild as a small **responsive** view instead of the 600×800
hardcoded page. The `/api/v1/snapshot` contract and renderers stay.

Cost reality: no modern-browser e-ink device is as cheap as a used Kindle (the
cheap e-ink hardware *is* the locked stuff). Budget ~$80–200 used.

Device options, cheapest first:

| Option | Notes |
|---|---|
| Used Android e-ink **phone** (Hisense A5/A7/A9) | Cheapest path to a modern browser (~$80–150 used). Phone-sized ~5–6"; mostly China-market imports (check bands/region). |
| Budget Android e-ink **tablet** (Meebook, Bigme) | Full Android + sideload; cheaper than Boox, variable software quality. **Meebook S6 seen ~$160 CAD on AliExpress (2026-06-21)** — candidate. |
| **Onyx Boox** (Page/Poke/Leaf/Note/Tab) | Best-supported, recent Android, good refresh controls. ~$150–400+ new; the "just works" pick. |

### B. Keep the Kindle → render elsewhere, push pixels (rejected for this goal)

Bypass the browser: render the page to a PNG on the Pi (headless Chromium /
`wkhtmltoimage`) and push it to the Kindle framebuffer with **FBInk** (gives true
full-refresh waveform control → ghosting solved), looped via a KUAL extension.
Projects like *kindle-dash* do exactly this.

Downside that rules it out here: it loses browser interactivity. Tap nav would
require reading the Kindle's touch input device (`/dev/input/event*`) and mapping
regions to view changes — real work. Good for a read-only display; not for the
"interface vehicle" direction wanted.

## Buy checklist (before committing to any Android e-ink device)

Must-haves:

- **Real Android with APK sideload** (to install Fully Kiosk / a browser). A
  listing vague about "Android" or only touting a reading app is a red flag.
- **Android 10+** (current system WebView/Chrome engine).
- **2.4 GHz Wi-Fi** (5 GHz a bonus) to reach the Pi/LAN.

For 24/7 wall use:

- Runs continuously on USB power, screen-always-on (Fully Kiosk can force this).
  Expect battery wear from permanent charging — a consumable on a fixed display.
- OS **e-ink refresh-mode control** (full/Regal) to manage ghosting.
- **Frontlight** that can be turned *off* for the matte wall look.

Lower-stakes: no security updates / some bloat on cheap brands — acceptable since
the device only talks to the LAN and shows our own page. Quality/longevity vary.

## Open questions / next steps

- Confirm the Meebook S6's actual Android version, resolution, sideload support,
  and refresh modes before buying (AliExpress listings relabel; specs unverified).
- When a device is in hand: build a responsive variant of the display, validate
  Fully Kiosk + auto-refresh + a real bottom-pinned footer and full-height tap
  regions, then strip the Kindle workarounds from `web_display.py`.
- Decide whether the old Kindle stays as a fallback/second display.

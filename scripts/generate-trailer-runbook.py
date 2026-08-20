#!/usr/bin/env python3
"""Generate the printable trailer power system operating runbook.

Single-column layout, deliberately: the trailer procedures are short enough to
fit one column, and every one of them is order-critical (battery before PV, PV
breaker open before the panels are handled). A single column gives one
unambiguous top-to-bottom reading path with no mid-procedure column jump, and
on a page this sparse it buys substantially larger type.

Body type is auto-scaled to the largest size that still fits on one page, so
the poster stays as legible as the content allows without hand-tuning.

Content is parsed from the "## Runbook" section of docs/trailer-power-system.md
so the posted procedure and the documented one cannot drift apart.
"""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs/trailer-power-system.md"
DEFAULT_OUTPUT = ROOT / "output/pdf/trailer-power-runbook.pdf"

TITLE = "TRAILER POWER SYSTEM"
REVISION = "REV 2026-08-09"

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 26
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

INK = HexColor("#141414")
MID_GRAY = HexColor("#5A5A5A")
BAND_GRAY = HexColor("#EFEFEF")
RULE_GRAY = HexColor("#B4B4B4")

# Physical control labels and switch positions, bolded so the eye can match
# printed text to the label on the equipment. Longest first: the regex
# alternation is leftmost-first and must not match "ON" inside a longer label.
EMPHASIS_TERMS = [
    "BATTERY TO CHARGE CONTROLLER",
    "PV TO CHARGE CONTROLLER",
    "MASTER SWITCH",
    "MASTER",
    "120 VAC",
    "POWER",
    "OFF",
    "ON",
]
EMPHASIS_RE = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in EMPHASIS_TERMS) + r")\b"
)

# Sections of the runbook that belong on the posted card, mapped to their
# printed heading and layout treatment. "Why the order matters" is explanatory
# background and stays in the repo doc.
WANTED = {
    "General information": ("GENERAL INFO", "info"),
    "Using the inverter": ("USING THE INVERTER", "steps"),
    "Charging the batteries": ("CHARGING THE BATTERIES", "steps"),
    "Shutting down the system": ("SHUTTING DOWN THE SYSTEM", "steps"),
}


@dataclass
class Section:
    title: str
    items: list[str] = field(default_factory=list)
    kind: str = "steps"


def parse_runbook(path: Path) -> list[Section]:
    """Pull the runbook subsections out of the trailer system doc."""
    sections: list[Section] = []
    current: Section | None = None
    in_item = False
    in_runbook = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if raw.startswith("## "):
            # Entering the runbook, or leaving it for the next top section.
            in_runbook = line[3:].strip() == "Runbook"
            continue
        if not in_runbook:
            continue

        if raw.startswith("### "):
            heading = line[4:].strip()
            current = None
            in_item = False
            if heading in WANTED:
                printed, kind = WANTED[heading]
                current = Section(printed, kind=kind)
                sections.append(current)
            continue

        if current is None:
            continue
        if not line:
            in_item = False
            continue

        match = re.match(r"^(?:\d+\.|-)\s+(.+)$", line)
        if match:
            current.items.append(match.group(1))
            in_item = True
        elif in_item:
            # Continuation of a list item wrapped across source lines.
            current.items[-1] += " " + line

    missing = {printed for printed, _ in WANTED.values()} - {s.title for s in sections}
    if missing:
        raise ValueError(f"Missing runbook sections in {path}: {sorted(missing)}")
    return sections


def markup(text: str, confirmations: bool = True) -> str:
    """Bold control labels; italicise the verify-the-result sentences.

    The italic pass separates "what you do" from "what you should observe",
    and applies only inside numbered procedure steps. The general-info
    bullets are standing rules, not things to check, so they are left upright
    even though some of them contain the word "should".
    """
    out: list[str] = []
    for sentence in re.split(r"(?<=\.)\s+", text):
        if not sentence:
            continue
        body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(sentence))
        body = EMPHASIS_RE.sub(r"<b>\1</b>", body)
        confirms = sentence.startswith("Verify") or "should" in sentence
        out.append(f"<i>{body}</i>" if confirmations and confirms else body)
    return " ".join(out)


def build(sections: list[Section], f: float, canvas: Canvas | None = None) -> float:
    """Lay out the page at body font size `f`. Returns the height consumed.

    Drawing is skipped when `canvas` is None, so the same code both measures
    and renders and the two can never disagree.
    """
    title_f = f * 1.75
    head_f = f * 1.10
    bar_h = f * 2.05
    # Keep the chip at or below one line of text: sized larger, it pads every
    # single-line step to chip height and silently costs the whole page a
    # point of body size.
    chip = f * 1.32
    step_gap = f * 0.58
    sect_gap = f * 1.10

    body = ParagraphStyle(
        "body", fontName="Helvetica", fontSize=f, leading=f * 1.30, textColor=INK
    )
    bullet = ParagraphStyle(
        "bullet", fontName="Helvetica", fontSize=f, leading=f * 1.30, textColor=INK
    )

    y = PAGE_HEIGHT - MARGIN

    # --- Title -------------------------------------------------------------
    if canvas:
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", title_f)
        canvas.drawString(MARGIN, y - title_f, TITLE)
        canvas.setFillColor(MID_GRAY)
        canvas.setFont("Helvetica-Bold", f * 0.62)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, y - title_f, REVISION)
    y -= title_f + f * 0.42
    if canvas:
        canvas.setStrokeColor(INK)
        canvas.setLineWidth(2.2)
        canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    y -= sect_gap

    for section in sections:
        if section.kind == "caution":
            # A black label bar over a heavy-ruled box. Drawn as one merged
            # unit: the box is stroked first and the filled bar laid over its
            # top edge, so the two read as a single bordered block.
            pad = f * 0.62
            inner_w = CONTENT_WIDTH - 2 * pad - f * 1.1
            paras = [Paragraph(markup(t, confirmations=False), bullet) for t in section.items]
            heights = [p.wrap(inner_w, PAGE_HEIGHT)[1] for p in paras]
            box_h = bar_h + pad + sum(heights) + step_gap * (len(paras) - 1) + pad

            if canvas:
                canvas.setStrokeColor(INK)
                canvas.setLineWidth(1.6)
                canvas.setFillColor(white)
                canvas.roundRect(
                    MARGIN, y - box_h, CONTENT_WIDTH, box_h, 4, fill=1, stroke=1
                )
                canvas.setFillColor(INK)
                canvas.roundRect(
                    MARGIN, y - bar_h, CONTENT_WIDTH, bar_h, 4, fill=1, stroke=0
                )
                canvas.setFillColor(white)
                canvas.setFont("Helvetica-Bold", head_f)
                canvas.drawString(
                    MARGIN + f * 0.7, y - bar_h + (bar_h - head_f) / 2 + f * 0.12,
                    section.title,
                )

            item_y = y - bar_h - pad
            for para, height in zip(paras, heights):
                if canvas:
                    canvas.setFillColor(INK)
                    canvas.circle(
                        MARGIN + pad + f * 0.24, item_y - f * 0.52, f * 0.16,
                        fill=1, stroke=0,
                    )
                    para.drawOn(canvas, MARGIN + pad + f * 1.1, item_y - height)
                item_y -= height + step_gap
            y -= box_h + sect_gap
            continue

        if section.kind == "info":
            # General info: a tinted band, set apart from the procedures.
            pad = f * 0.58
            inner_w = CONTENT_WIDTH - 2 * pad - f * 1.1
            paras = [Paragraph(markup(t, confirmations=False), bullet) for t in section.items]
            heights = [p.wrap(inner_w, PAGE_HEIGHT)[1] for p in paras]
            band_h = sum(heights) + step_gap * (len(paras) - 1) + 2 * pad + head_f + pad

            if canvas:
                canvas.setFillColor(BAND_GRAY)
                canvas.roundRect(
                    MARGIN, y - band_h, CONTENT_WIDTH, band_h, 4, fill=1, stroke=0
                )
                canvas.setFillColor(MID_GRAY)
                canvas.setFont("Helvetica-Bold", head_f * 0.82)
                canvas.drawString(MARGIN + pad, y - pad - head_f * 0.82, section.title)

            item_y = y - pad - head_f - pad * 0.35
            for para, height in zip(paras, heights):
                if canvas:
                    canvas.setFillColor(INK)
                    canvas.circle(
                        MARGIN + pad + f * 0.24, item_y - f * 0.52, f * 0.16, fill=1, stroke=0
                    )
                    para.drawOn(canvas, MARGIN + pad + f * 1.1, item_y - height)
                item_y -= height + step_gap
            y -= band_h + sect_gap
            continue

        # --- Procedure heading bar ----------------------------------------
        if canvas:
            canvas.setFillColor(INK)
            canvas.roundRect(MARGIN, y - bar_h, CONTENT_WIDTH, bar_h, 3, fill=1, stroke=0)
            canvas.setFillColor(white)
            canvas.setFont("Helvetica-Bold", head_f)
            canvas.drawString(MARGIN + f * 0.7, y - bar_h + (bar_h - head_f) / 2 + f * 0.12,
                              section.title)
        y -= bar_h + f * 0.62

        text_x = MARGIN + chip + f * 0.72
        text_w = CONTENT_WIDTH - chip - f * 0.72

        for number, text in enumerate(section.items, start=1):
            para = Paragraph(markup(text), body)
            _, height = para.wrap(text_w, PAGE_HEIGHT)
            row = max(chip, height)

            if canvas:
                chip_y = y - chip
                canvas.setFillColor(INK)
                canvas.roundRect(MARGIN, chip_y, chip, chip, 3, fill=1, stroke=0)
                canvas.setFillColor(white)
                canvas.setFont("Helvetica-Bold", f * 0.98)
                canvas.drawCentredString(
                    MARGIN + chip / 2, chip_y + chip / 2 - f * 0.35, str(number)
                )
                para.drawOn(canvas, text_x, y - height)
            y -= row + step_gap

        y -= sect_gap - step_gap
        if canvas and section is not sections[-1]:
            canvas.setStrokeColor(RULE_GRAY)
            canvas.setLineWidth(0.7)
            canvas.line(MARGIN, y + sect_gap * 0.45, PAGE_WIDTH - MARGIN, y + sect_gap * 0.45)

    return PAGE_HEIGHT - MARGIN - y


def fit(sections: list[Section]) -> float:
    """Largest body size that still fits the page, to 0.05 pt."""
    available = PAGE_HEIGHT - 2 * MARGIN
    lo, hi = 7.0, 20.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if build(sections, mid) <= available:
            lo = mid
        else:
            hi = mid
    return round(lo - 0.05, 2)


def generate(source: Path, output: Path) -> None:
    sections = parse_runbook(source)
    size = fit(sections)
    output.parent.mkdir(parents=True, exist_ok=True)

    canvas = Canvas(str(output), pagesize=letter)
    canvas.setTitle("Trailer Power System - Operating Runbook")
    canvas.setAuthor("Magpie Camp Power System")
    canvas.setSubject("Posted operator procedure for the utility trailer 12 V system")
    build(sections, size, canvas)
    canvas.showPage()
    canvas.save()

    used = build(sections, size)
    print(f"{output}: body {size} pt, {used:.0f}/{PAGE_HEIGHT - 2 * MARGIN:.0f} pt used")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source, args.output)


if __name__ == "__main__":
    main()

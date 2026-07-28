#!/usr/bin/env python3
"""Generate the printable system-power operator quick reference."""

from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs/runbooks/system-power-quick-reference.md"
DEFAULT_OUTPUT = ROOT / "output/pdf/system-power-quick-reference.pdf"

PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 27
COLUMN_GAP = 18
COLUMN_WIDTH = (PAGE_WIDTH - 2 * MARGIN - COLUMN_GAP) / 2
INK = HexColor("#171717")
MID_GRAY = HexColor("#5A5A5A")
LIGHT_GRAY = HexColor("#ECECEC")
RULE_GRAY = HexColor("#B8B8B8")


@dataclass
class Step:
    number: int
    text: str


@dataclass
class Section:
    title: str
    steps: list[Step] = field(default_factory=list)


def parse_markdown(path: Path) -> tuple[str, list[Section]]:
    title = ""
    sections: list[Section] = []
    current: Section | None = None
    current_step: Step | None = None

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            current = Section(line[3:].strip())
            sections.append(current)
            current_step = None
            continue

        match = re.match(r"^(\d+)\.\s+(.+)$", line)
        if match and current is not None:
            current_step = Step(int(match.group(1)), match.group(2))
            current.steps.append(current_step)
        elif current_step is not None:
            current_step.text += " " + line

    if not title or len(sections) != 4:
        raise ValueError(f"Expected one title and four sections in {path}")
    return title, sections


def inline_markup(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def draw_title(canvas: Canvas, title: str) -> float:
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 20)
    title_width = stringWidth(title.upper(), "Helvetica-Bold", 20)
    canvas.drawString(MARGIN, PAGE_HEIGHT - MARGIN - 17, title.upper())

    revision = "REV 2026-07-27"
    revision_width = stringWidth(revision, "Helvetica-Bold", 7.5)
    if title_width + revision_width + 16 < PAGE_WIDTH - 2 * MARGIN:
        canvas.setFillColor(MID_GRAY)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN - 15, revision)

    y = PAGE_HEIGHT - MARGIN - 29
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(2)
    canvas.line(MARGIN, y, PAGE_WIDTH - MARGIN, y)
    return y - 12


def draw_section(
    canvas: Canvas,
    section: Section,
    x: float,
    y_top: float,
) -> float:
    heading_height = 29
    canvas.setFillColor(INK)
    canvas.roundRect(
        x,
        y_top - heading_height,
        COLUMN_WIDTH,
        heading_height,
        3,
        fill=1,
        stroke=0,
    )
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 12.2)
    canvas.drawCentredString(
        x + COLUMN_WIDTH / 2,
        y_top - 19,
        section.title.upper(),
    )

    step_style = ParagraphStyle(
        "step",
        fontName="Helvetica",
        fontSize=11.5,
        leading=14.2,
        textColor=black,
        alignment=0,
        spaceAfter=0,
    )

    y = y_top - heading_height - 9
    number_size = 24
    text_x = x + number_size + 9
    text_width = COLUMN_WIDTH - number_size - 12

    for step in section.steps:
        paragraph = Paragraph(inline_markup(step.text), step_style)
        _, paragraph_height = paragraph.wrap(text_width, PAGE_HEIGHT)
        row_height = max(number_size, paragraph_height)
        is_conditional = "IF PV ARRAY 1 IS INSTALLED" in step.text

        if is_conditional:
            canvas.setFillColor(LIGHT_GRAY)
            canvas.roundRect(
                x - 3,
                y - row_height - 3,
                COLUMN_WIDTH + 6,
                row_height + 6,
                3,
                fill=1,
                stroke=0,
            )

        number_y = y - number_size
        canvas.setFillColor(INK)
        canvas.roundRect(
            x,
            number_y,
            number_size,
            number_size,
            3,
            fill=1,
            stroke=0,
        )
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(
            x + number_size / 2,
            number_y + 6.3,
            str(step.number),
        )

        paragraph.drawOn(canvas, text_x, y - paragraph_height)
        y -= row_height + 10

    canvas.setStrokeColor(RULE_GRAY)
    canvas.setLineWidth(0.7)
    canvas.line(x, y + 2, x + COLUMN_WIDTH, y + 2)
    return y - 11


def generate(source: Path, output: Path) -> None:
    title, sections = parse_markdown(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    canvas = Canvas(str(output), pagesize=letter)
    canvas.setTitle(title)
    canvas.setAuthor("Magpie Camp Power System")
    canvas.setSubject("Equipment-side operator power procedure")

    top = draw_title(canvas, title)
    left_y = draw_section(canvas, sections[0], MARGIN, top)
    draw_section(canvas, sections[1], MARGIN, left_y)

    right_x = MARGIN + COLUMN_WIDTH + COLUMN_GAP
    right_y = draw_section(canvas, sections[2], right_x, top)
    draw_section(canvas, sections[3], right_x, right_y)

    canvas.showPage()
    canvas.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.source, args.output)


if __name__ == "__main__":
    main()

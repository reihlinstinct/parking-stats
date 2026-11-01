"""Tiny dependency-free SVG charts for the landing page (aggregate data).

Counts only in, escaped labels only out: the SVG is embedded inline in
index.html, so every label runs through html.escape and every number is
an int count. No scripts, no external resources.
"""

from __future__ import annotations

import html

INK = "#1e293b"
BAR = "#2563eb"
BAR_ALT = "#0d9488"
GRID = "#cbd5e1"


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def vbar_chart(rows: list[tuple[str, int]], *, color: str = BAR) -> str:
    """Vertical bars, e.g. monthly counts; labels below the axis."""
    if not rows:
        return ""
    width, height, pad_b, pad_t = 560, 180, 34, 18
    top = max(count for _, count in rows) or 1
    slot = width / len(rows)
    bar_w = max(6.0, slot * 0.62)
    parts = [
        f'<svg viewBox="0,0,{width},{height}" role="img" '
        f'aria-label="{len(rows)} עמודות" xmlns="http://www.w3.org/2000/svg">'
    ]
    y_base = height - pad_b
    span = y_base - pad_t
    for i, (label, count) in enumerate(rows):
        h = max(2.0, span * count / top)
        x = i * slot + (slot - bar_w) / 2
        y = y_base - h
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
            f'rx="3" fill="{color}"><title>{_e(label)}: {count}</title></rect>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
            f'font-size="11" fill="{INK}">{count}</text>'
        )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - 18:.1f}" text-anchor="middle" '
            f'font-size="10" fill="{INK}" transform="rotate(-30,{x + bar_w / 2:.1f},'
            f'{height - 18:.1f})">{_e(label)}</text>'
        )
    parts.append(
        f'<line x1="0" y1="{y_base}" x2="{width}" y2="{y_base}" stroke="{GRID}"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def hbar_chart(rows: list[tuple[str, int]], *, color: str = BAR_ALT) -> str:
    """Horizontal bars, e.g. top categories/streets; label right of bar (RTL)."""
    if not rows:
        return ""
    row_h = 30
    width = 560
    label_w = 220
    max_label_chars = 30
    height = row_h * len(rows) + 8
    top = max(count for _, count in rows) or 1
    span = width - label_w - 50
    parts = [
        f'<svg viewBox="0,0,{width},{height}" role="img" '
        f'aria-label="{len(rows)} שורות" xmlns="http://www.w3.org/2000/svg">'
    ]
    for i, (label, count) in enumerate(rows):
        y = i * row_h + 6
        w = max(2.0, span * count / top)
        # Truncate long labels so they stay inside the reserved column and
        # never collide with the count sitting left of a short bar; the
        # full label is on the bar's <title>.
        shown = (
            label
            if len(label) <= max_label_chars
            else label[: max_label_chars - 1] + "…"
        )
        parts.append(
            f'<text x="{width - 4}" y="{y + 15}" direction="rtl" '
            f'text-anchor="start" font-size="12" fill="{INK}">{_e(shown)}</text>'
        )
        x = width - label_w - w
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="20" rx="4" '
            f'fill="{color}"><title>{_e(label)}: {count}</title></rect>'
        )
        # direction="ltr": the SVG inherits direction:rtl from the page,
        # and for weak-direction digits "end" would anchor the LEFT edge,
        # painting the count over the bar.
        parts.append(
            f'<text x="{x - 6:.1f}" y="{y + 15}" direction="ltr" '
            f'text-anchor="end" font-size="12" fill="{INK}">{count}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)

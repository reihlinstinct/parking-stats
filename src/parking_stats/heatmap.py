"""Public street-level heat map for the Pages site (#1).

Counts only, matching the aggregate-only rule: the input is the floored
by_street map from summary.json (street -> count), coordinates come from
a private mapping file passed to the publisher with ``--geocoding`` (the
mapping is a build input, like the filing ledger, and never enters this
repository), every emitted coordinate is rounded to ~3 decimals
(~111 m), counts are floored per street, and streets below the threshold
never appear. The rendered page is fully static - inline SVG and a
table, no scripts and no external resources - so viewing it contacts no
third party. Unmapped or below-threshold streets are counted, never
shown.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypedDict
from xml.sax.saxutils import escape

MIN_STREET_REPORTS = 4
COORD_DECIMALS = 3

# Rough Israel bounds; a mapping entry outside them is a data error.
_LAT_RANGE = (29.0, 34.0)
_LNG_RANGE = (33.0, 36.5)

_SVG_WIDTH = 760
_SVG_HEIGHT = 480
_SVG_PAD = 46


class Cell(TypedDict):
    lat: float
    lng: float
    n: int
    label: str


class _AggCell(TypedDict):
    lat: float
    lng: float
    n: int
    names: list[str]


def load_mapping(path: Path) -> dict[str, tuple[float, float]]:
    """Read a private street -> (lat, lng) mapping file and validate it."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("geocoding mapping must be a JSON object")
    mapping: dict[str, tuple[float, float]] = {}
    for key, value in raw.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, list)
            or len(value) != 2
            or any(
                not isinstance(v, (int, float)) or isinstance(v, bool) for v in value
            )
            or any(not math.isfinite(v) for v in value)
        ):
            raise ValueError(f"geocoding mapping: bad entry for {key!r}")
        lat, lng = float(value[0]), float(value[1])
        if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]) or not (
            _LNG_RANGE[0] <= lng <= _LNG_RANGE[1]
        ):
            raise ValueError(f"geocoding mapping: out-of-bounds entry for {key!r}")
        mapping[key] = (lat, lng)
    return mapping


def build_cells(
    by_street: dict[str, int], mapping: dict[str, tuple[float, float]]
) -> tuple[list[Cell], int, int]:
    """Floor, round and aggregate street counts into map cells.

    Returns (cells, below_threshold_count, unmapped_count); both counts
    are surfaced in the JSON payload and the page note. Streets that
    round to the same cell share it: counts are summed and the label
    joins the street names with " / ". Cells carry only rounded
    coordinates, a count and a street-level label, and are sorted by
    count descending.
    """
    # Names accumulate as a list so a street name containing " / "
    # cannot corrupt the join/split round trip.
    aggregated: dict[tuple[float, float], _AggCell] = {}
    below = 0
    unmapped = 0
    for street, count in by_street.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"by_street: bad count for {street!r}")
        if count < MIN_STREET_REPORTS:
            below += 1
            continue
        coords = mapping.get(street)
        if coords is None:
            unmapped += 1
            continue
        key = (round(coords[0], COORD_DECIMALS), round(coords[1], COORD_DECIMALS))
        cell = aggregated.setdefault(
            key, {"lat": key[0], "lng": key[1], "n": 0, "names": []}
        )
        cell["n"] += count
        cell["names"].append(street)
    cells: list[Cell] = [
        {
            "lat": raw["lat"],
            "lng": raw["lng"],
            "n": raw["n"],
            "label": " / ".join(sorted(raw["names"])),
        }
        for raw in aggregated.values()
    ]
    cells.sort(key=lambda c: (-c["n"], c["label"]))
    return cells, below, unmapped


def build_heatmap_json(
    cells: list[Cell], unmapped: int, below: int, updated: str
) -> str:
    payload = {
        "schema_version": 2,
        "aggregation": "street",
        "threshold": MIN_STREET_REPORTS,
        "rounding_decimals": COORD_DECIMALS,
        "updated": updated,
        "unmapped_streets": unmapped,
        "below_threshold_streets": below,
        "cells": cells,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def _svg(cells: list[Cell]) -> str:
    """Draw the cells as an inline SVG scatter; labels stay in <title>."""
    if not cells:
        return ""
    lats = [c["lat"] for c in cells]
    lngs = [c["lng"] for c in cells]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    lat_span = (max_lat - min_lat) or 0.01
    lng_span = (max_lng - min_lng) or 0.01
    max_n = max(c["n"] for c in cells)

    def x(lng: float) -> float:
        return _SVG_PAD + (lng - min_lng) / lng_span * (_SVG_WIDTH - 2 * _SVG_PAD)

    def y(lat: float) -> float:
        return (
            _SVG_HEIGHT
            - _SVG_PAD
            - (lat - min_lat) / lat_span * (_SVG_HEIGHT - 2 * _SVG_PAD)
        )

    parts = [
        f'<svg width="{_SVG_WIDTH}" height="{_SVG_HEIGHT}" role="img" '
        'aria-label="מפת מוקדים חמים ברמת רחוב">',
        f'<rect width="{_SVG_WIDTH}" height="{_SVG_HEIGHT}" fill="#f7f5f0"/>',
    ]
    for cell in cells:
        ratio = cell["n"] / max_n
        radius = round(8 + 26 * math.sqrt(ratio), 1)
        opacity = round(0.25 + 0.55 * ratio, 2)
        label = escape(f"{cell['label']}: {cell['n']} דיווחים", {'"': "&quot;"})
        parts.append(
            f'<circle cx="{x(cell["lng"]):.1f}" cy="{y(cell["lat"]):.1f}" '
            f'r="{radius}" fill="#c0392b" fill-opacity="{opacity}" '
            f'stroke="#7b241c" stroke-width="1">'
            f"<title>{label}</title></circle>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _streets_note(count: int, suffix: str) -> str:
    """Hebrew note line with singular/plural grammar."""
    if count == 1:
        return f"<p>רחוב נוסף אחד {suffix}.</p>"
    return f"<p>{count} רחובות נוספים {suffix}.</p>"


def build_heatmap_html(
    cells: list[Cell], unmapped: int, below: int, updated: str
) -> str:
    rows = "".join(
        f"<tr><td>{escape(c['label'])}</td><td>{c['n']}</td></tr>" for c in cells
    )
    table = (
        "<table><thead><tr><th>רחוב</th><th>דיווחים</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if cells
        else ""
    )
    notes = ""
    if below:
        notes += _streets_note(below, "מתחת לסף ולכן אינו מוצג")
    if unmapped:
        notes += _streets_note(unmapped, "מעל הסף ללא מיפוי")
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'">
<title>מפת חום - דיווחי 106 ירושלים</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; color: #222; }}
header {{ padding: 1rem 1.5rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 .4rem; }}
header p {{ margin: .2rem 0; }}
main {{ padding: 0 1.5rem; }}
svg {{ max-width: 100%; height: auto; border: 1px solid #ddd; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ border: 1px solid #ccc; padding: .3rem .8rem; text-align: right; }}
footer {{ padding: .6rem 1.5rem; color: #555; font-size: .85rem; }}
</style>
</head>
<body>
<header>
<h1>מפת חום - מוקדי דיווחים ברמת רחוב</h1>
<p>רחובות עם {MIN_STREET_REPORTS} דיווחים ומעלה בלבד, ברזולוציה מעוגנת
של כ-111 מטר. ספירות בלבד - ללא נתונים גולמיים, לוחיות רישוי או שמות.
הדף סטטי: ללא סקריפטים וללא גורמים חיצוניים.</p>
<p><a href="index.html">חזרה לדף הראשי</a></p>
</header>
<main>
{_svg(cells)}
{table}
{notes}
</main>
<footer>נתונים מצטברים בלבד &middot; עודכן: {escape(updated)}</footer>
</body>
</html>
"""

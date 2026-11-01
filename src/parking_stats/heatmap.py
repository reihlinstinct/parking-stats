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
shown. A simplified OpenStreetMap street-line layer (public ODbL data,
vendored by scripts/fetch_basemap.py) can be rendered as a same-origin
basemap under the heat bubbles (#23); viewing the page still contacts
no third party.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
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
# The overlay projects onto a 76x48 grid (same aspect as 760x480): small
# numbers keep the generated markup well clear of the privacy scanner's
# 7-8 digit-run rules, and a viewBox lets the overlay scale with the
# basemap <img> it sits on.
_GRID_W = 76.0
_GRID_H = 48.0
_GRID_PAD = 4.6

# Colour ramp for the heat gradient (low -> high count).
_HEAT_RAMP = ("#ffffcc", "#fed976", "#fd8d3c", "#f03b20", "#bd0026")


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


def load_basemap(path: Path) -> list[list[tuple[float, float]]]:
    """Read the vendored OSM street-line layer and validate it (#23).

    The file is public OpenStreetMap geometry (ODbL) written by
    scripts/fetch_basemap.py: a JSON object with a "lines" list of
    polylines, each a list of [lat, lng] pairs inside the Israel bounds.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise ValueError("basemap: expected a JSON object with a lines list")
    lines: list[list[tuple[float, float]]] = []
    for entry in raw["lines"]:
        if not isinstance(entry, list) or len(entry) < 2:
            raise ValueError("basemap: every line needs at least two points")
        line: list[tuple[float, float]] = []
        for point in entry:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    not isinstance(v, (int, float)) or isinstance(v, bool)
                    for v in point
                )
                or any(not math.isfinite(v) for v in point)
            ):
                raise ValueError("basemap: bad point")
            lat, lng = float(point[0]), float(point[1])
            if not (_LAT_RANGE[0] <= lat <= _LAT_RANGE[1]) or not (
                _LNG_RANGE[0] <= lng <= _LNG_RANGE[1]
            ):
                raise ValueError("basemap: out-of-bounds point")
            line.append((lat, lng))
        lines.append(line)
    return lines


def _projection(
    cells: list[Cell],
) -> tuple[
    Callable[[float, float], tuple[float, float]], tuple[float, float, float, float]
]:
    """Linear lat/lng -> grid projection shared by basemap and overlay."""
    lats = [c["lat"] for c in cells]
    lngs = [c["lng"] for c in cells]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    lat_span = (max_lat - min_lat) or 0.01
    lng_span = (max_lng - min_lng) or 0.01
    pad_lat = max(0.004, lat_span * 0.08)
    pad_lng = max(0.004, lng_span * 0.08)
    min_lat -= pad_lat
    max_lat += pad_lat
    min_lng -= pad_lng
    max_lng += pad_lng
    lat_span = max_lat - min_lat
    lng_span = max_lng - min_lng
    bbox = (min_lat, min_lng, max_lat, max_lng)

    def project(lat: float, lng: float) -> tuple[float, float]:
        gx = _GRID_PAD + (lng - min_lng) / lng_span * (_GRID_W - 2 * _GRID_PAD)
        gy = (
            _GRID_H - _GRID_PAD - (lat - min_lat) / lat_span * (_GRID_H - 2 * _GRID_PAD)
        )
        return gx, gy

    return project, bbox


def build_basemap_svg(lines: list[list[tuple[float, float]]], cells: list[Cell]) -> str:
    """Project the vendored street lines into a static basemap SVG (#23).

    The output shares the overlay's projection so the two line up
    exactly. Lines are clipped to the cells' padded bounding box;
    coordinates use a comma between x and y so no long digit runs form.
    Contains public OSM geometry only - no counts, names or addresses.
    """
    project, (min_lat, min_lng, max_lat, max_lng) = _projection(cells)
    commands: list[str] = []

    def emit(run: list[tuple[float, float]]) -> None:
        if len(run) < 2:
            return
        first = True
        for gx, gy in run:
            cmd = "M" if first else "L"
            commands.append(f"{cmd}{gx:.2f},{gy:.2f}")
            first = False

    for line in lines:
        run: list[tuple[float, float]] = []
        for lat, lng in line:
            if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
                run.append(project(lat, lng))
            else:
                emit(run)
                run = []
        emit(run)
    if not commands:
        return ""
    path = "".join(commands)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="480"'
        ' viewBox="0 0 76 48" role="img" aria-label="מפת רחובות">'
        f'<rect width="76" height="48" fill="#f7f5f0"/>'
        f'<path d="{path}" fill="none" stroke="#c9c4bb" stroke-width="0.18"'
        ' stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    )


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


def _overlay_svg(cells: list[Cell], basemap: bool) -> str:
    """Draw the heat layer: gradient blobs, centre dots and labels (#23).

    Pure static SVG: one radialGradient per intensity bucket, one shared
    blur filter, a ramp legend and street-name labels (the #20 ask). No
    scripts, no external resources. With a basemap the svg carries a
    viewBox and sits transparent over the same-origin <img>.
    """
    if not cells:
        return ""
    project, _bbox = _projection(cells)
    max_n = max(c["n"] for c in cells)
    min_n = min(c["n"] for c in cells)

    defs = [
        "<defs>",
        '<filter id="soft"><feGaussianBlur stdDeviation="0.55"/></filter>',
    ]
    for i, colour in enumerate(_HEAT_RAMP):
        defs.append(
            f'<radialGradient id="g{i}">'
            f'<stop offset="0%" stop-color="{colour}" stop-opacity="0.9"/>'
            f'<stop offset="70%" stop-color="{colour}" stop-opacity="0.45"/>'
            f'<stop offset="100%" stop-color="{colour}" stop-opacity="0"/>'
            "</radialGradient>"
        )
    defs.append("</defs>")

    parts = [
        f'<svg width="{_SVG_WIDTH}" height="{_SVG_HEIGHT}" viewBox="0 0 76 48"'
        ' role="img" aria-label="מפת מוקדים חמים ברמת רחוב">',
        "".join(defs),
    ]
    if not basemap:
        parts.append('<rect width="76" height="48" fill="#f7f5f0"/>')
    # Labels: a cell gets its street name directly on the map only when
    # a candidate position fits without overlapping an already-placed
    # label (honest ~0.85 units/glyph boxes); otherwise it falls back to
    # its table row number next to the dot, and the table's # column
    # carries the name. No label is ever hidden under another.
    placed: list[tuple[float, float, float, float]] = []  # x0, y0, x1, y1
    labels: list[str] = []
    for index, cell in enumerate(cells, 1):
        ratio = cell["n"] / max_n
        intensity = math.sqrt(ratio)
        bucket = min(len(_HEAT_RAMP) - 1, int(intensity * len(_HEAT_RAMP)))
        radius = round(1.3 + 2.4 * intensity, 2)
        gx, gy = project(cell["lat"], cell["lng"])
        title = escape(f"{cell['label']}: {cell['n']} דיווחים", {'"': "&quot;"})
        parts.append(
            f'<circle cx="{gx:.2f}" cy="{gy:.2f}" r="{radius}" '
            f'fill="url(#g{bucket})" filter="url(#soft)">'
            f"<title>{title}</title></circle>"
        )
        parts.append(
            f'<circle cx="{gx:.2f}" cy="{gy:.2f}" r="0.45" '
            f'fill="{_HEAT_RAMP[bucket]}" fill-opacity="0.95"/>'
        )
        name = escape(cell["label"])
        half = len(cell["label"]) * 0.85
        height = 2.0
        candidates = [
            (gx, gy - radius - 0.6),
            (gx, gy + radius + 2.1),
            (gx, gy - radius - 2.8),
            (gx, gy + radius + 4.3),
        ]
        chosen: tuple[float, float] | None = None
        for cx, cy in candidates:
            box = (cx - half, cy - height, cx + half, cy + 0.4)
            if cx - half < 0 or cx + half > _GRID_W:
                continue
            if not any(
                box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1]
                for p in placed
            ):
                chosen = (cx, cy)
                break
        if chosen is not None:
            placed.append(
                (
                    chosen[0] - half,
                    chosen[1] - height,
                    chosen[0] + half,
                    chosen[1] + 0.4,
                )
            )
            labels.append(
                f'<text x="{chosen[0]:.2f}" y="{chosen[1]:.2f}" '
                'text-anchor="middle" font-size="1.5" fill="#222" '
                'stroke="#fff" stroke-width="0.3" paint-order="stroke">'
                f"{name}</text>"
            )
        else:
            labels.append(
                f'<text x="{gx:.2f}" y="{gy - radius - 0.4:.2f}" '
                'text-anchor="middle" font-size="1.4" fill="#111" '
                'stroke="#fff" stroke-width="0.28" paint-order="stroke">'
                f"{index}</text>"
            )
    parts.extend(labels)
    # Ramp legend, bottom-left.
    lx, ly = 2.0, 44.6
    # RTL text with anchor=end grows rightward from x, so the Hebrew
    # legend label is not clipped at the map's left edge.
    parts.append(
        f'<text x="{lx}" y="{ly - 1.2}" font-size="1.5" fill="#333" '
        f'text-anchor="end">דיווחים: {min_n} - {max_n}</text>'
    )
    for i, colour in enumerate(_HEAT_RAMP):
        parts.append(
            f'<rect x="{lx + i * 3.1:.2f}" y="{ly}" width="2.9" height="1.6" '
            f'fill="{colour}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _streets_note(count: int, suffix: str) -> str:
    """Hebrew note line with singular/plural grammar."""
    if count == 1:
        return f"<p>רחוב נוסף אחד {suffix}.</p>"
    return f"<p>{count} רחובות נוספים {suffix}.</p>"


def build_heatmap_html(
    cells: list[Cell],
    unmapped: int,
    below: int,
    updated: str,
    basemap: bool = False,
) -> str:
    rows = "".join(
        f"<tr><td>{i}</td><td>{escape(c['label'])}</td><td>{c['n']}</td></tr>"
        for i, c in enumerate(cells, 1)
    )
    table = (
        "<table><thead><tr><th>#</th><th>רחוב</th><th>דיווחים</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if cells
        else ""
    )
    notes = ""
    if below:
        notes += _streets_note(below, "מתחת לסף ולכן אינו מוצג")
    if unmapped:
        notes += _streets_note(unmapped, "מעל הסף ללא מיפוי")
    if basemap:
        map_block = (
            '<div class="map">'
            '<img src="data/basemap.svg" width="760" height="480" alt="">'
            + _overlay_svg(cells, basemap=True)
            + "</div>"
        )
    else:
        map_block = _overlay_svg(cells, basemap=False)
    attribution = (
        " &middot; מפת הרקע: &copy; OpenStreetMap contributors (ODbL)"
        if basemap
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'">
<title>מפת חום - דיווחי 106 ירושלים</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; color: #222; }}
header {{ padding: 1rem 1.5rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 .4rem; }}
header p {{ margin: .2rem 0; }}
main {{ padding: 0 1.5rem; }}
svg {{ max-width: 100%; height: auto; }}
.map {{ position: relative; max-width: 760px; border: 1px solid #ddd; }}
.map img {{ display: block; width: 100%; height: auto; }}
.map svg {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
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
{map_block}
{table}
{notes}
</main>
<footer>נתונים מצטברים בלבד &middot; עודכן: {escape(updated)}{attribution}</footer>
</body>
</html>
"""

"""Public street-level heat map for the Pages site (#1, #23, #28).

Counts only, matching the aggregate-only rule: the input is the floored
by_street map from summary.json (street -> count), coordinates come from
a private mapping file passed to the publisher with ``--geocoding`` (the
mapping is a build input, like the filing ledger, and never enters this
repository), every emitted coordinate is rounded to ~3 decimals
(~111 m), counts are floored per street, and streets below the threshold
never appear. Unmapped or below-threshold streets are counted, never
shown. A simplified OpenStreetMap street-line layer (public ODbL data,
vendored by scripts/fetch_basemap.py) is inlined as a same-origin
basemap under the heat bubbles.

The map is a single SVG whose initial viewBox is cropped to the dense
cluster and centred on the hottest cells; a small first-party script
(data/map.js, served same-origin under script-src 'self') adds wheel,
pinch and drag zoom/pan with zoom capped well above the published
~111 m cell resolution. Without JavaScript the page still renders the
centred static view. Viewing the page contacts no third party: no CDN,
no tiles, no external requests of any kind.
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

# The map projects onto a 76x48 grid: small numbers keep the generated
# markup well clear of the privacy scanner's 7-8 digit-run rules, and a
# viewBox lets the same SVG zoom and pan.
_GRID_W = 76.0
_GRID_H = 48.0
_GRID_PAD = 4.6
# Extra lat/lng margin around the cells' bounding box so panning still
# has basemap content outside the initial view.
_WORLD_PAD_SHARE = 0.35
_WORLD_PAD_MIN = 0.006
# The initial view covers the smallest prefix of cells (sorted by count
# descending) holding this share of all mapped reports, then expands to
# the grid aspect with a margin. Cells outside it stay in the table and
# are revealed by zooming or panning.
_VIEW_REPORTS_SHARE = 0.9
_VIEW_MARGIN = 1.3
_VIEW_MIN_SPAN = 10.0
# Max zoom in map.js: the smallest viewBox span. Nothing finer than the
# published ~111 m cells exists, so zoom stops well above cell level.
MAX_ZOOM_MIN_SPAN = 6.0

# Colour ramp for the heat gradient (low -> high count).
_HEAT_RAMP = ("#fed976", "#fd8d3c", "#f03b20", "#bd0026", "#800026")

_Box = tuple[float, float, float, float]


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
    """Linear lat/lng -> grid projection shared by basemap and overlay.

    The world bbox is the cells' bounding box plus a wide margin so
    panning outside the initial view still shows basemap streets.
    """
    lats = [c["lat"] for c in cells]
    lngs = [c["lng"] for c in cells]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)
    lat_span = (max_lat - min_lat) or 0.01
    lng_span = (max_lng - min_lng) or 0.01
    pad_lat = max(_WORLD_PAD_MIN, lat_span * _WORLD_PAD_SHARE)
    pad_lng = max(_WORLD_PAD_MIN, lng_span * _WORLD_PAD_SHARE)
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


def build_basemap_path(
    lines: list[list[tuple[float, float]]], cells: list[Cell]
) -> str:
    """Project the vendored street lines into one SVG path string (#23).

    The output shares the overlay's projection so the two line up
    exactly. Lines are clipped to the cells' (widely padded) bounding
    box; coordinates use a comma between x and y so no long digit runs
    form. Contains public OSM geometry only - no counts, names or
    addresses.
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
    return "".join(commands)


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
        "unmapped_streets": unmapped,
        "below_threshold_streets": below,
        "cells": cells,
        "updated": updated,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def _radius(cell: Cell, max_n: int) -> float:
    return round(1.3 + 2.4 * math.sqrt(cell["n"] / max_n), 2)


def _overlaps(box: _Box, placed: list[_Box]) -> bool:
    return any(
        box[0] < p[2] and box[2] > p[0] and box[1] < p[3] and box[3] > p[1]
        for p in placed
    )


def _fallback_box(
    gx: float, gy: float, radius: float, index: int, scale: float = 1.0
) -> _Box:
    """Collision box of a numbered fallback label next to its dot."""
    half = len(str(index)) * 0.85 * scale
    y = gy - radius - 0.4 * scale
    return (gx - half, y - 2.0 * scale, gx + half, y + 0.4 * scale)


def _render_cells(
    cells: list[Cell],
    project: Callable[[float, float], tuple[float, float]],
    scale: float = 1.0,
    view: tuple[float, float, float, float] | None = None,
) -> tuple[list[str], list[_Box]]:
    """Emit circle and label SVG fragments; return them with the boxes.

    A cell gets its street name directly on the map only when a
    candidate position fits without overlapping an already-placed label
    (honest ~0.85 units/glyph boxes); otherwise it falls back to its
    table row number next to the dot, and the table's # column carries
    the name. Fallback numbers are registered in the collision boxes
    too, so no label is ever hidden under another label or a number.
    """
    max_n = max(c["n"] for c in cells)
    parts: list[str] = []
    placed: list[_Box] = []
    labels: list[str] = []
    for index, cell in enumerate(cells, 1):
        bucket = min(
            len(_HEAT_RAMP) - 1,
            int(math.sqrt(cell["n"] / max_n) * len(_HEAT_RAMP)),
        )
        radius = _radius(cell, max_n)
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
        half = len(cell["label"]) * 0.85 * scale
        height = 2.0 * scale
        candidates = [
            (gx, gy - radius - 0.6 * scale),
            (gx, gy + radius + 2.1 * scale),
            (gx, gy - radius - 2.8 * scale),
            (gx, gy + radius + 4.3 * scale),
        ]
        # Cells inside the initial view keep their labels inside it too:
        # a name that would clip at the view's edge falls through to a
        # position within the view (typically below the dot) so the
        # hottest streets stay named in the default view (#30).
        in_view = view is None or (
            view[0] <= gx <= view[0] + view[2] and view[1] <= gy <= view[1] + view[3]
        )
        chosen: tuple[float, float] | None = None
        for cx, cy in candidates:
            box = (cx - half, cy - height, cx + half, cy + 0.4)
            if cx - half < 0 or cx + half > _GRID_W:
                continue
            if (
                in_view
                and view is not None
                and not (
                    view[0] <= box[0]
                    and box[2] <= view[0] + view[2]
                    and view[1] <= box[1]
                    and box[3] <= view[1] + view[3]
                )
            ):
                continue
            if not _overlaps(box, placed):
                chosen = (cx, cy)
                break
        if chosen is not None:
            box = (
                chosen[0] - half,
                chosen[1] - height,
                chosen[0] + half,
                chosen[1] + 0.4,
            )
            placed.append(box)
            labels.append(
                f'<text x="{chosen[0]:.2f}" y="{chosen[1]:.2f}" '
                f'text-anchor="middle" font-size="{1.5 * scale:.2f}" '
                f'fill="#222" stroke="#fff" stroke-width="{0.3 * scale:.2f}" '
                f'paint-order="stroke">{name}</text>'
            )
        else:
            placed.append(_fallback_box(gx, gy, radius, index, scale))
            labels.append(
                f'<text x="{gx:.2f}" y="{gy - radius - 0.4 * scale:.2f}" '
                f'text-anchor="middle" font-size="{1.4 * scale:.2f}" '
                f'fill="#111" stroke="#fff" stroke-width="{0.28 * scale:.2f}" '
                f'paint-order="stroke">{index}</text>'
            )
    parts.extend(labels)
    return parts, placed


def _initial_view(
    cells: list[Cell], project: Callable[[float, float], tuple[float, float]]
) -> tuple[tuple[float, float, float, float], int]:
    """Initial viewBox cropped to the dense cluster, and the outside count.

    The view wraps the smallest prefix of cells (sorted by count
    descending) holding _VIEW_REPORTS_SHARE of all mapped reports,
    expanded to the grid aspect with a margin, clamped to the world.
    Returns ((x, y, w, h), number of cells outside the view).
    """
    total = sum(c["n"] for c in cells)
    covered = 0
    prefix: list[Cell] = []
    for cell in cells:
        prefix.append(cell)
        covered += cell["n"]
        if covered >= _VIEW_REPORTS_SHARE * total:
            break
    points = [project(c["lat"], c["lng"]) for c in prefix]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    span_w = max(max(xs) - min(xs), 0.1) * _VIEW_MARGIN
    span_h = max(max(ys) - min(ys), 0.1) * _VIEW_MARGIN
    # Match the grid aspect so the SVG needs no letterboxing.
    aspect = _GRID_H / _GRID_W
    w = max(span_w, span_h / aspect, _VIEW_MIN_SPAN)
    h = w * aspect
    max_w = min(_GRID_W, _GRID_H / aspect)
    w = min(w, max_w)
    h = w * aspect
    x = min(max(cx - w / 2, 0.0), _GRID_W - w)
    y = min(max(cy - h / 2, 0.0), _GRID_H - h)
    outside = 0
    for cell in cells:
        gx, gy = project(cell["lat"], cell["lng"])
        if not (x <= gx <= x + w and y <= gy <= y + h):
            outside += 1
    return (x, y, w, h), outside


def _map_svg(cells: list[Cell], basemap_path: str) -> tuple[str, int]:
    """The interactive map SVG; returns (markup, outside-view count)."""
    project, _bbox = _projection(cells)
    (vx, vy, vw, vh), outside = _initial_view(cells, project)
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
    # Comma separators (valid SVG): they keep the scanner's compact-plate
    # rule from reading the four viewBox numbers as one digit run.
    view = f"{vx:.2f},{vy:.2f},{vw:.2f},{vh:.2f}"
    parts = [
        f'<svg id="map" viewBox="{view}"'
        f' width="960" height="{960 * vh / vw:.0f}"'
        ' role="img" aria-label="מפת מוקדים חמים ברמת רחוב"'
        f' data-home="{view}"'
        f' data-world="0,0,{_GRID_W:.0f},{_GRID_H:.0f}"'
        f' data-min-span="{MAX_ZOOM_MIN_SPAN:.0f}">',
        "".join(defs),
        f'<rect width="{_GRID_W:.0f}" height="{_GRID_H:.0f}" fill="#f7f5f0"/>',
    ]
    if basemap_path:
        parts.append(
            # Non-scaling stroke: the basemap keeps a constant screen
            # width at every zoom level instead of thickening on zoom-in.
            f'<path d="{basemap_path}" fill="none" stroke="#c9c4bb"'
            ' stroke-width="1.1" vector-effect="non-scaling-stroke"'
            ' stroke-linecap="round" stroke-linejoin="round"/>'
        )
    # Label metrics scale with the initial viewBox: text renders at a
    # readable size in the cropped view and grows/shrinks with zoom;
    # the dots, which represent geography, keep their grid size.
    cell_parts, _placed = _render_cells(
        cells, project, scale=vw / _GRID_W, view=(vx, vy, vw, vh)
    )
    parts.extend(cell_parts)
    parts.append("</svg>")
    return "".join(parts), outside


def _streets_note(count: int, singular: str, plural: str | None = None) -> str:
    """Hebrew note line with singular/plural grammar."""
    if count == 1:
        return f"<p>רחוב נוסף אחד {singular}.</p>"
    return f"<p>{count} רחובות נוספים {plural or singular}.</p>"


def _outside_note(outside: int) -> str:
    """Note that cells outside the initial view are not dropped (#28)."""
    hint = "מופיעים בטבלה, ואפשר לראותם על המפה בעזרת הזזה או הקטנה"
    if outside == 1:
        return f"<p>מוקד נוסף אחד מחוץ לתצוגה הראשונית - {hint}.</p>"
    return f"<p>{outside} מוקדים נוספים מחוץ לתצוגה הראשונית - {hint}.</p>"


MAP_JS = """"use strict";
(function () {
  var svg = document.getElementById("map");
  if (!svg || !svg.getAttribute("data-home")) return;
  var controls = document.getElementById("map-controls");
  if (controls) controls.hidden = false;
  function nums(name) {
    return svg.getAttribute(name).split(",").map(Number);
  }
  var home = nums("data-home");
  var world = nums("data-world");
  var minSpan = Number(svg.getAttribute("data-min-span")) || 6;
  var aspect = home[3] / home[2];
  var maxSpan = Math.min(world[2], world[3] / aspect);
  var cx = home[0] + home[2] / 2;
  var cy = home[1] + home[3] / 2;
  var span = home[2];
  function apply() {
    span = Math.min(Math.max(span, minSpan), maxSpan);
    var w = span;
    var h = span * aspect;
    cx = Math.min(Math.max(cx, world[0] + w / 2), world[0] + world[2] - w / 2);
    cy = Math.min(Math.max(cy, world[1] + h / 2), world[1] + world[3] - h / 2);
    svg.setAttribute(
      "viewBox",
      (cx - w / 2) + "," + (cy - h / 2) + "," + w + "," + h
    );
  }
  function toSvg(clientX, clientY) {
    var pt = svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    return pt.matrixTransform(svg.getScreenCTM().inverse());
  }
  function zoom(factor, px, py) {
    var next = Math.min(Math.max(span * factor, minSpan), maxSpan);
    var k = next / span;
    cx = px + (cx - px) * k;
    cy = py + (cy - py) * k;
    span = next;
    apply();
  }
  svg.addEventListener(
    "wheel",
    function (e) {
      e.preventDefault();
      var p = toSvg(e.clientX, e.clientY);
      zoom(Math.pow(1.002, e.deltaY), p.x, p.y);
    },
    { passive: false }
  );
  var pointers = new Map();
  var lastDist = 0;
  svg.addEventListener("pointerdown", function (e) {
    svg.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      var pts = Array.from(pointers.values());
      lastDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    }
  });
  svg.addEventListener("pointermove", function (e) {
    if (!pointers.has(e.pointerId)) return;
    var prev = pointers.get(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 1) {
      var rect = svg.getBoundingClientRect();
      cx -= ((e.clientX - prev.x) * span) / rect.width;
      cy -= ((e.clientY - prev.y) * span * aspect) / rect.height;
      apply();
    } else if (pointers.size === 2) {
      var pts = Array.from(pointers.values());
      var d = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      if (lastDist > 0 && d > 0) {
        var p = toSvg((pts[0].x + pts[1].x) / 2, (pts[0].y + pts[1].y) / 2);
        zoom(lastDist / d, p.x, p.y);
      }
      lastDist = d;
    }
  });
  function drop(e) {
    pointers.delete(e.pointerId);
    lastDist = 0;
  }
  svg.addEventListener("pointerup", drop);
  svg.addEventListener("pointercancel", drop);
  if (controls) {
    controls.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      if (b.hasAttribute("data-reset")) {
        cx = home[0] + home[2] / 2;
        cy = home[1] + home[3] / 2;
        span = home[2];
        apply();
      } else if (b.getAttribute("data-zoom") === "in") {
        zoom(0.7, cx, cy);
      } else if (b.getAttribute("data-zoom") === "out") {
        zoom(1 / 0.7, cx, cy);
      }
    });
  }
  svg.style.touchAction = "none";
})();
"""


def build_heatmap_html(
    cells: list[Cell],
    unmapped: int,
    below: int,
    updated: str,
    basemap_lines: list[list[tuple[float, float]]] | None = None,
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
        notes += _streets_note(
            below, "מתחת לסף ולכן אינו מוצג", "מתחת לסף ולכן אינם מוצגים"
        )
    if unmapped:
        notes += _streets_note(unmapped, "מעל הסף ללא מיפוי")
    basemap_path = build_basemap_path(basemap_lines, cells) if basemap_lines else ""
    if cells:
        svg, outside = _map_svg(cells, basemap_path)
        if outside:
            notes = _outside_note(outside) + notes
        # RTL legend: the scale runs right-to-left for a Hebrew reader -
        # lowest on the right, highest on the left - and the label order
        # matches ("max - min").
        min_n = min(c["n"] for c in cells)
        max_n = max(c["n"] for c in cells)
        swatches = "".join(
            f'<span class="sw" style="background:{colour}"></span>'
            for colour in _HEAT_RAMP
        )
        legend = (
            '<div class="legend" aria-label="מקרא צבעים">'
            '<span class="legend-range">דיווחים: '
            f'<span dir="ltr">{max_n} - {min_n}</span></span>'
            f"{swatches}</div>"
        )
        map_block = (
            '<div class="mapwrap">'
            '<div class="controls" id="map-controls" hidden>'
            '<button type="button" data-zoom="in" title="הגדלה">+</button>'
            '<button type="button" data-zoom="out" title="הקטנה">&minus;</button>'
            '<button type="button" data-reset title="חזרה לתצוגה הראשונית">'
            "איפוס</button></div>"
            + svg
            + "</div>"
            + legend
            + "<noscript><p>ללא JavaScript מוצגת תצוגה סטטית של האזור החם; "
            "כל המוקדים מופיעים בטבלה.</p></noscript>"
            '<script src="data/map.js" defer></script>'
        )
    else:
        map_block = ""
        legend = ""
    # The ODbL attribution anchor is a plain hyperlink: it costs no
    # request until clicked and stays within default-src 'none'.
    attribution = (
        " &middot; מפת הרקע: &copy; "
        '<a href="https://www.openstreetmap.org/copyright">'
        "OpenStreetMap contributors</a> (ODbL)"
        if basemap_path
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy"
 content="default-src 'none'; style-src 'unsafe-inline'; script-src 'self'">
<title>מפת חום - דיווחי 106 ירושלים</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; color: #222; }}
header {{ padding: 1rem 1.5rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 .4rem; }}
header p {{ margin: .2rem 0; }}
main {{ padding: 0 1.5rem; }}
.mapwrap {{ position: relative; max-width: 960px; border: 1px solid #ddd; }}
.mapwrap svg {{ display: block; width: 100%; height: auto; }}
.controls {{ position: absolute; top: .4rem; inset-inline-start: .4rem;
  display: flex; gap: .3rem; z-index: 1; }}
.controls[hidden] {{ display: none; }}
.mapwrap svg, .mapwrap text {{ user-select: none; -webkit-user-select: none; }}
.controls button {{ font-size: 1rem; min-width: 2rem; padding: .2rem .5rem;
  background: rgba(255,255,255,.92); border: 1px solid #999;
  border-radius: .25rem; cursor: pointer; }}
.legend {{ display: flex; align-items: center; gap: .15rem; margin: .4rem 0;
  font-size: .85rem; color: #333; }}
.legend .legend-range {{ margin-inline-end: .5rem; }}
.legend .sw {{ display: inline-block; width: 1.6rem; height: .8rem; }}
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
הדף מוגש כולו מאותו מקור: ללא סקריפטים של צד שלישי וללא בקשות חיצוניות.</p>
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

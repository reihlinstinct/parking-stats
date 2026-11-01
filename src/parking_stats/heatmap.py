"""Public hotspot heat map for the Pages site (#1).

Counts only, matching the aggregate-only rule: the input is the floored
hotspot map from summary.json (street+house -> count), coordinates come
from the committed, reviewed geocoding mapping (no live geocoding in the
build), every emitted point is rounded to ~3 decimals (~111 m) and counts
are aggregated at that rounded level, and spots below the threshold never
appear. Unmapped or below-threshold spots are counted, never shown.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypedDict

MIN_SPOT_REPORTS = 3
COORD_DECIMALS = 3

DEFAULT_MAPPING = Path(__file__).with_name("geocoding.json")


class Point(TypedDict):
    lat: float
    lng: float
    n: int
    label: str

# Rough Israel bounds; a mapping entry outside them is a data error.
_LAT_RANGE = (29.0, 34.0)
_LNG_RANGE = (33.0, 36.5)

PAGE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>מפת חום - דיווחי 106 ירושלים</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
body { font-family: system-ui, sans-serif; margin: 0; }
header { padding: 1rem 1.5rem; }
h1 { font-size: 1.4rem; margin: 0 0 .4rem; }
header p { margin: .2rem 0; }
#map { height: 78vh; width: 100%; }
footer { padding: .6rem 1.5rem; color: #555; font-size: .85rem; }
#error { color: #900; padding: 0 1.5rem; }
</style>
</head>
<body>
<header>
<h1>מפת חום - מוקדי דיווחים</h1>
<p>מוקדים עם __THRESHOLD__ דיווחים ומעלה בלבד, ברזולוציה מעוגנת של כ-111
מטר. ספירות בלבד - ללא נתונים גולמיים, לוחיות רישוי או שמות.</p>
<p><a href="index.html">חזרה לדף הראשי</a></p>
</header>
<div id="map"></div>
<p id="error"></p>
<footer>נתונים מצטברים בלבד &middot; עודכן: <span id="updated">-</span></footer>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js"></script>
<script>
fetch('data/heatmap.json').then(function (r) { return r.json(); }).then(function (payload) {
  var map = L.map('map');
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  var pts = payload.points.map(function (p) { return [p.lat, p.lng, p.n]; });
  L.heatLayer(pts, {}).addTo(map);
  if (pts.length) {
    map.fitBounds(L.latLngBounds(pts.map(function (p) { return [p[0], p[1]]; })).pad(0.25));
  } else {
    map.setView([31.78, 35.21], 12);
  }
  payload.points.forEach(function (p) {
    L.circleMarker([p.lat, p.lng], { radius: 8, opacity: 0, fillOpacity: 0 })
      .bindTooltip(p.label + ': ' + p.n + ' דיווחים')
      .addTo(map);
  });
  document.getElementById('updated').textContent = payload.updated;
}).catch(function () {
  document.getElementById('error').textContent = 'טעינת נתוני המפה נכשלה.';
});
</script>
</body>
</html>
"""


def load_mapping(path: Path = DEFAULT_MAPPING) -> dict[str, tuple[float, float]]:
    """Read the committed street+house -> (lat, lng) mapping and validate it."""
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


def build_points(
    hotspots: dict[str, int], mapping: dict[str, tuple[float, float]]
) -> tuple[list[Point], int, int]:
    """Round, aggregate and floor the hotspot counts.

    Returns (points, below_threshold_count, unmapped_count). Points carry
    only lat/lng/count/label and are sorted by count descending.
    """
    aggregated: dict[tuple[float, float], Point] = {}
    below = 0
    unmapped = 0
    for spot, count in hotspots.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"hotspots: bad count for {spot!r}")
        if count < MIN_SPOT_REPORTS:
            below += 1
            continue
        coords = mapping.get(spot)
        if coords is None:
            unmapped += 1
            continue
        key = (round(coords[0], COORD_DECIMALS), round(coords[1], COORD_DECIMALS))
        point = aggregated.setdefault(
            key, {"lat": key[0], "lng": key[1], "n": 0, "label": spot}
        )
        point["n"] += count
    points = sorted(aggregated.values(), key=lambda p: (-p["n"], p["label"]))
    return points, below, unmapped


def build_heatmap_json(points: list[Point], unmapped: int, updated: str) -> str:
    payload = {
        "schema_version": 1,
        "threshold": MIN_SPOT_REPORTS,
        "rounding_decimals": COORD_DECIMALS,
        "updated": updated,
        "unmapped_spots": unmapped,
        "points": points,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def build_heatmap_html() -> str:
    return PAGE.replace("__THRESHOLD__", str(MIN_SPOT_REPORTS))

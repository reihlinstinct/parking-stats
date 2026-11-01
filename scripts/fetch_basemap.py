#!/usr/bin/env python3
"""Vendor a simplified OSM street-line layer for the heat map (#23).

Runs ONCE at vendoring time (network: Overpass API); the published build
stays offline and reads only the committed vendor/basemap-jerusalem.json.
The geometry is public OpenStreetMap data (ODbL) - street centrelines
only, no addresses, no private data. Attribution is rendered on the page.

Usage: python scripts/fetch_basemap.py
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

BBOX = (31.725, 35.165, 31.805, 35.230)  # south, west, north, east
QUERY = (
    "[out:json][timeout:60];"
    'way["highway"~"^(primary|secondary|tertiary|residential|unclassified)$"]'
    f"({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});out geom;"
)
URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "parking-stats-basemap-vendor (reihlinstinct@gmail.com)"
TOLERANCE_DEG = 0.00015  # ~15 m, below the ~8 m/px render resolution
DECIMALS = 4  # ~11 m
OUT = Path(__file__).resolve().parent.parent / "vendor" / "basemap-jerusalem.json"


def _perp_distance(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _douglas_peucker(points, tol):
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        dmax, index = 0.0, -1
        for i in range(first + 1, last):
            d = _perp_distance(points[i], points[first], points[last])
            if d > dmax:
                dmax, index = d, i
        if dmax > tol and index > 0:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


def main() -> int:
    request = urllib.request.Request(
        URL,
        data=("data=" + urllib.parse.quote(QUERY)).encode(),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=100) as response:
        data = json.load(response)
    lines = []
    points_in = points_out = 0
    for element in data.get("elements", []):
        if element.get("type") != "way":
            continue
        geometry = element.get("geometry") or []
        line = [
            (round(p["lat"], DECIMALS), round(p["lon"], DECIMALS)) for p in geometry
        ]
        points_in += len(line)
        line = _douglas_peucker(line, TOLERANCE_DEG)
        points_out += len(line)
        if len(line) >= 2:
            lines.append(line)
    payload = {
        "source": "OpenStreetMap contributors (ODbL) via Overpass API",
        "bbox": list(BBOX),
        "tolerance_deg": TOLERANCE_DEG,
        "decimals": DECIMALS,
        "lines": lines,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(
        f"{len(lines)} lines, {points_in}->{points_out} points "
        f"({OUT.stat().st_size // 1024} KiB) -> {OUT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

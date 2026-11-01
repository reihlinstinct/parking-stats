"""Publish a local `p106-stats build` output directory into public/ (#4).

The raw municipal exports never leave the machine that runs `p106-stats`;
only its aggregate output directory reaches this publisher. Every file is
re-scanned with the privacy rules before anything is copied, and a single
finding aborts the publish with nothing written. GitHub Pages then serves
public/ as-is.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import heatmap as heatmap_mod
from .charts import hbar_chart, vbar_chart
from .privacy_scan import PLATES_SECTION_RULES, scan_lines

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "monthly.csv": ("month", "reports"),
    "categories.csv": ("category", "reports"),
    "streets.csv": ("street", "reports"),
    "plates.csv": ("plate", "reports"),
}
EXPECTED_SUMMARY_KEYS = {
    "schema_version",
    "total_incidents",
    "date_range",
    "by_month",
    "by_week",
    "by_category",
    "by_street",
    "by_status",
    "by_neighborhood",
    "hotspots",
    "closure",
    "inputs",
    "duplicates_skipped",
    "repeat_plates_count",
}

REQUIRED = (
    "summary.json",
    "monthly.csv",
    "categories.csv",
    "streets.csv",
    "report.html",
)
DATA_FILES = ("summary.json", "monthly.csv", "categories.csv", "streets.csv")
# Optional: the helper's repeat-plates aggregate. Published only when the
# build emits it; a stale published copy is removed when it does not.
OPTIONAL_DATA = ("plates.csv",)
# Canonical dashed standard plates (7 or 8 digits) and a count of at
# least the repeat threshold the aggregate is approved for.
_PLATE_ROW = re.compile(r"\d{2}-\d{3}-\d{2}|\d{3}-\d{2}-\d{3}", re.ASCII)
_COUNT_ROW = re.compile(r"\d+", re.ASCII)
MIN_REPEAT_REPORTS = 3

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #f1f5f9; color: #1e293b;
  font-family: "Segoe UI", Arial, "Noto Sans Hebrew", sans-serif;
  line-height: 1.55;
}
header {
  background: linear-gradient(135deg, #1e3a8a, #2563eb);
  color: #ffffff; padding: 2.2rem 1rem 1.8rem; text-align: center;
}
header h1 { margin: 0 0 .4rem; font-size: 1.7rem; }
header p { margin: 0 auto; max-width: 42rem; opacity: .95; }
main { max-width: 60rem; margin: 0 auto; padding: 1.2rem 1rem 2.5rem; }
.cards {
  display: grid; gap: .8rem; margin: -2.2rem 0 1.6rem;
  grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr));
}
.card {
  background: #ffffff; border-radius: .8rem; padding: .9rem 1rem;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .12);
}
.card .num { font-size: 1.45rem; font-weight: 700; color: #1e3a8a; }
.card .cap { font-size: .85rem; color: #475569; }
section {
  background: #ffffff; border-radius: .8rem; padding: 1rem 1.2rem;
  margin-bottom: 1.2rem; box-shadow: 0 1px 3px rgba(15, 23, 42, .12);
}
section h2 { margin: .2rem 0 .8rem; font-size: 1.15rem; color: #1e3a8a; }
svg { width: 100%; height: auto; display: block; }
ul.links { padding: 0; margin: 0; list-style: none; display: flex; flex-wrap: wrap; gap: .6rem; }
ul.links a {
  display: inline-block; background: #eff6ff; color: #1d4ed8;
  border: 1px solid #bfdbfe; border-radius: .6rem; padding: .45rem .9rem;
  text-decoration: none; font-weight: 600;
}
ul.links a:focus-visible, ul.links a:hover { background: #dbeafe; }
table { border-collapse: collapse; margin: .2rem 0 .4rem; }
th, td { padding: .35rem .8rem; border-bottom: 1px solid #e2e8f0; text-align: right; }
th { color: #1e3a8a; font-size: .9rem; }
footer { text-align: center; color: #475569; font-size: .85rem; padding: 0 1rem 2rem; }
"""

PAGE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>דיווחי 106 - ירושלים</title>
<style>{css}</style>
</head>
<body>
<header>
<h1>דיווחי 106 - ירושלים</h1>
<p>כל הנתונים כאן מצטברים בלבד: ספירות לפי חודש, שבוע, קטגוריה, רחוב
ושכונה - ללא נתונים גולמיים או שמות. לוחיות רישוי מוצגות רק לרכבים
שדווחו שלוש פעמים ומעלה, יחד עם מספר הדיווחים בלבד.</p>
</header>
<main>
<div class="cards">{cards}</div>
<section><h2>דיווחים לפי חודש (12 האחרונים)</h2>{monthly_chart}</section>
<section><h2>קטגוריות מובילות</h2>{categories_chart}</section>
<section><h2>רחובות מובילים</h2>{streets_chart}</section>
{plates_section}
<section><h2>הדוח המלא והנתונים</h2>
<ul class="links">
<li><a href="report.html">הדוח המלא עם כל הגרפים</a></li>
<li><a href="data/summary.json">summary.json</a> (סיכום מכונה-קריא)</li>
<li><a href="data/monthly.csv">טבלה חודשית</a></li>
<li><a href="data/categories.csv">קטגוריות</a></li>
<li><a href="data/streets.csv">רחובות</a></li>
{plates_link}
{heatmap_link}
</ul>
</section>
</main>
<footer>מקור הנתונים: {label} &middot; עודכן: {updated} &middot; נתונים מצטברים בלבד</footer>
</body>
</html>
"""


def _read_counts(path: Path) -> list[tuple[str, int]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return [(name, int(count)) for name, count in rows[1:] if len(rows) > 1]


def _median_text(days: float) -> str:
    minutes = round(days * 24 * 60)
    if minutes < 60:
        return f"כ-{minutes} דקות"
    hours = round(minutes / 60)
    if hours < 48:
        return f"כ-{hours} שעות"
    return f"כ-{round(days)} ימים"


MONTHS_HE = (
    "ינואר",
    "פברואר",
    "מרץ",
    "אפריל",
    "מאי",
    "יוני",
    "יולי",
    "אוגוסט",
    "ספטמבר",
    "אוקטובר",
    "נובמבר",
    "דצמבר",
)


def _month_year(iso_date: str) -> str:
    """'2021-02-24' -> 'פברואר 2021' (also keeps digit chains out of the page)."""
    try:
        year, month, _ = iso_date.split("-", 2)
        return f"{MONTHS_HE[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return iso_date


def _card(number: str, caption: str) -> str:
    return (
        f'<div class="card"><div class="num">{html.escape(number)}</div>'
        f'<div class="cap">{html.escape(caption)}</div></div>'
    )


def _plates_section(plates: list[tuple[str, int]]) -> str:
    """Repeat-plates block: plate + report count only, inside the markers
    the privacy scanner's section:plates directive recognises."""
    if not plates:
        return ""
    rows = "".join(
        '<tr><td dir="ltr">' + html.escape(plate) + "</td><td>"
        f"{count} דיווחים</td></tr>"
        for plate, count in plates
    )
    return (
        "<section><h2>רכבים חוזרים</h2>\n"
        "<p>רכבים שדווחו שלוש פעמים ומעלה; מוצגים מספר הרישוי ומספר "
        "הדיווחים בלבד.</p>\n"
        + "<!-- repeat-plates:begin -->\n"
        + "<table><thead><tr><th>מספר רישוי</th><th>דיווחים</th></tr></thead>"
        + "<tbody>"
        + rows
        + "</tbody></table>\n"
        + "<!-- repeat-plates:end -->\n"
        + "</section>"
    )


def _read_plates(input_dir: Path) -> list[tuple[str, int]]:
    path = input_dir / "plates.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or tuple(rows[0]) != EXPECTED_COLUMNS["plates.csv"]:
        raise ValueError("plates.csv: unexpected header")
    plates: list[tuple[str, int]] = []
    for row in rows[1:]:
        if len(row) != 2 or not _PLATE_ROW.fullmatch(row[0]):
            raise ValueError("plates.csv: non-canonical plate")
        if not _COUNT_ROW.fullmatch(row[1]):
            raise ValueError("plates.csv: non-numeric count")
        value = int(row[1])
        if value < MIN_REPEAT_REPORTS:
            raise ValueError("plates.csv: count below repeat threshold")
        plates.append((row[0], value))
    return plates


def build_index(
    input_dir: Path, label: str, updated: str, *, heatmap_available: bool = False
) -> str:
    """Render the landing page from the aggregate output files."""
    summary = json.loads((input_dir / "summary.json").read_text(encoding="utf-8"))
    monthly = _read_counts(input_dir / "monthly.csv")
    categories = _read_counts(input_dir / "categories.csv")
    streets = _read_counts(input_dir / "streets.csv")

    by_month = summary.get("by_month") or {}
    peak = (
        _month_year(max(by_month.items(), key=lambda item: item[1])[0] + "-01")
        if by_month
        else "-"
    )
    date_range = summary.get("date_range") or {}
    period = " - ".join(
        _month_year(str(date_range.get(edge, ""))) for edge in ("first", "last")
    ).strip(" -")
    median_days = (summary.get("closure") or {}).get("median_days")

    cards = [
        _card(f"{summary['total_incidents']:,}", "דיווחים"),
        _card(period or "-", "תקופה"),
        _card(peak, "החודש העמוס"),
    ]
    if isinstance(median_days, (int, float)):
        cards.append(_card(_median_text(float(median_days)), "זמן טיפול חציוני"))

    plates = _read_plates(input_dir)
    plates_link = (
        '<li><a href="data/plates.csv">רכבים חוזרים</a></li>' if plates else ""
    )
    heatmap_link = (
        '<li><a href="heatmap.html">מפת חום של מוקדים חמים</a></li>'
        if heatmap_available
        else ""
    )
    return PAGE.format(
        css=CSS,
        cards="".join(cards),
        monthly_chart=vbar_chart(monthly[-12:]),
        categories_chart=hbar_chart(categories[:6]),
        streets_chart=hbar_chart(streets[:6]),
        plates_section=_plates_section(plates),
        plates_link=plates_link,
        heatmap_link=heatmap_link,
        label=html.escape(label),
        updated=html.escape(updated),
    )


def scan_output(input_dir: Path) -> list[str]:
    """Privacy-scan every publishable file; return finding descriptions."""
    findings: list[str] = []
    for name in REQUIRED:
        path = input_dir / name
        if not path.exists():
            findings.append(f"{name}: missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(str(f) for f in scan_lines(name, text.splitlines(), set()))
    plates_path = input_dir / "plates.csv"
    if plates_path.exists():
        text = plates_path.read_text(encoding="utf-8", errors="replace")
        # plates.csv is the one approved plate carrier: plate rules are
        # waived, every other rule still guards it.
        findings.extend(
            str(f)
            for f in scan_lines(
                "plates.csv", text.splitlines(), set(PLATES_SECTION_RULES)
            )
        )
    findings.extend(check_shape(input_dir))
    return findings


def check_shape(input_dir: Path) -> list[str]:
    """Only expected CSV columns and summary keys may be published."""
    import csv
    import json

    findings: list[str] = []
    for name, expected in EXPECTED_COLUMNS.items():
        path = input_dir / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        header = tuple(rows[0]) if rows else ()
        if header != expected:
            findings.append(f"{name}: unexpected columns {header!r}")
            continue
        if name == "plates.csv":
            for row in rows[1:]:
                if (
                    len(row) != 2
                    or not _PLATE_ROW.fullmatch(row[0])
                    or not _COUNT_ROW.fullmatch(row[1])
                    or int(row[1]) < MIN_REPEAT_REPORTS
                ):
                    findings.append("plates.csv: non-canonical row")
                    break
    summary = input_dir / "summary.json"
    if summary.exists():
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            findings.append("summary.json: top level must be a JSON object")
            payload = {}
        keys = set(payload)
        unexpected = keys - EXPECTED_SUMMARY_KEYS
        if unexpected:
            findings.append(f"summary.json: unexpected keys {sorted(unexpected)!r}")
        repeat_plates = payload.get("repeat_plates_count")
        if repeat_plates is not None and (
            not isinstance(repeat_plates, int)
            or isinstance(repeat_plates, bool)
            or repeat_plates < 0
        ):
            findings.append(
                f"summary.json: repeat_plates_count must be an int >= 0, "
                f"got {repeat_plates!r}"
            )
    return findings


def _build_heatmap(
    input_dir: Path, updated: str, geocoding: Path | None
) -> dict[str, str] | None:
    """Render heatmap.html + heatmap.json from floored street counts (#1).

    Returns {} when there is nothing to map or no private mapping was
    given (stale copies are removed by the caller), None on a data error.
    Coordinates come only from the private mapping file passed with
    --geocoding; it never enters this repository and the build stays
    offline.
    """
    if geocoding is None:
        return {}
    try:
        summary = json.loads((input_dir / "summary.json").read_text(encoding="utf-8"))
        by_street = summary.get("by_street") or {}
        if not isinstance(by_street, dict) or not by_street:
            return {}
        mapping = heatmap_mod.load_mapping(geocoding)
        cells, below, unmapped = heatmap_mod.build_cells(by_street, mapping)
        if not cells:
            return {}
        return {
            "heatmap.html": heatmap_mod.build_heatmap_html(
                cells, unmapped, below, updated
            ),
            "heatmap.json": heatmap_mod.build_heatmap_json(
                cells, unmapped, below, updated
            ),
        }
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"publish: heat map error: {exc}", file=sys.stderr)
        return None


def publish(
    input_dir: Path, public_dir: Path, label: str, geocoding: Path | None = None
) -> int:
    findings = scan_output(input_dir)
    # The label is free text that lands on the landing page; scan it with
    # the same privacy rules before anything is written.
    findings.extend(str(f) for f in scan_lines("--label", label.splitlines(), set()))
    if findings:
        for finding in findings:
            print(f"publish: refused: {finding}", file=sys.stderr)
        return 1
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    heatmap_files = _build_heatmap(input_dir, updated, geocoding)
    if heatmap_files is None:
        print("publish: refused: heat map build failed", file=sys.stderr)
        return 1
    try:
        index_text = build_index(
            input_dir, label, updated, heatmap_available=bool(heatmap_files)
        )
    except (ValueError, KeyError, TypeError) as exc:
        print(f"publish: refused: index build failed: {exc}", file=sys.stderr)
        return 1
    index_findings = [
        str(f)
        for f in scan_lines(
            "index.html", index_text.splitlines(), set(), allow_plates_section=True
        )
    ]
    if index_findings:
        for finding in index_findings:
            print(f"publish: refused: {finding}", file=sys.stderr)
        return 1
    for name, text in heatmap_files.items():
        findings = [str(f) for f in scan_lines(name, text.splitlines(), set())]
        if findings:
            for finding in findings:
                print(f"publish: refused: {finding}", file=sys.stderr)
            return 1
    data_dir = public_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, text in heatmap_files.items():
        target = data_dir / name if name.endswith(".json") else public_dir / name
        target.write_text(text, encoding="utf-8")
    for stale in (public_dir / "heatmap.html", data_dir / "heatmap.json"):
        if not heatmap_files and stale.exists():
            stale.unlink()
    shutil.copy2(input_dir / "report.html", public_dir / "report.html")
    for name in DATA_FILES:
        shutil.copy2(input_dir / name, data_dir / name)
    copied = len(REQUIRED)
    for name in OPTIONAL_DATA:
        src = input_dir / name
        dst = data_dir / name
        if src.exists():
            shutil.copy2(src, dst)
            copied += 1
        elif dst.exists():
            dst.unlink()
    (public_dir / "index.html").write_text(index_text, encoding="utf-8")
    print(f"publish: {copied} files -> {public_dir} ({label})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="parking-stats-publish",
        description="Copy a local p106-stats output directory into public/ "
        "behind the privacy gate.",
    )
    parser.add_argument("input", type=Path, help="p106-stats output directory")
    parser.add_argument(
        "--public", type=Path, default=Path("public"), help="public directory"
    )
    parser.add_argument(
        "--label",
        default="דיווחי 106 מצטברים",
        help="data-source label shown on the landing page",
    )
    parser.add_argument(
        "--geocoding",
        type=Path,
        default=(
            Path(os.environ["PARKING_STATS_GEOCODING"])
            if os.environ.get("PARKING_STATS_GEOCODING")
            else None
        ),
        help="private street -> coordinate mapping JSON (build input, never "
        "committed); without it the heat map is skipped",
    )
    args = parser.parse_args(argv)
    return publish(args.input, args.public, args.label, args.geocoding)


if __name__ == "__main__":
    raise SystemExit(main())

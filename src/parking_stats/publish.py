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
import shutil
import sys
from pathlib import Path

from .charts import hbar_chart, vbar_chart
from .privacy_scan import scan_lines

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "monthly.csv": ("month", "reports"),
    "categories.csv": ("category", "reports"),
    "streets.csv": ("street", "reports"),
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
}

REQUIRED = (
    "summary.json",
    "monthly.csv",
    "categories.csv",
    "streets.csv",
    "report.html",
)
DATA_FILES = ("summary.json", "monthly.csv", "categories.csv", "streets.csv")

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
ושכונה - ללא נתונים גולמיים, לוחיות רישוי או שמות.</p>
</header>
<main>
<div class="cards">{cards}</div>
<section><h2>דיווחים לפי חודש (12 האחרונים)</h2>{monthly_chart}</section>
<section><h2>קטגוריות מובילות</h2>{categories_chart}</section>
<section><h2>רחובות מובילים</h2>{streets_chart}</section>
<section><h2>הדוח המלא והנתונים</h2>
<ul class="links">
<li><a href="report.html">הדוח המלא עם כל הגרפים</a></li>
<li><a href="data/summary.json">summary.json</a> (סיכום מכונה-קריא)</li>
<li><a href="data/monthly.csv">טבלה חודשית</a></li>
<li><a href="data/categories.csv">קטגוריות</a></li>
<li><a href="data/streets.csv">רחובות</a></li>
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


def build_index(input_dir: Path, label: str, updated: str) -> str:
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

    return PAGE.format(
        css=CSS,
        cards="".join(cards),
        monthly_chart=vbar_chart(monthly[-12:]),
        categories_chart=hbar_chart(categories[:6]),
        streets_chart=hbar_chart(streets[:6]),
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
            header = tuple(next(csv.reader(handle), ()))
        if header != expected:
            findings.append(f"{name}: unexpected columns {header!r}")
    summary = input_dir / "summary.json"
    if summary.exists():
        try:
            keys = set(json.loads(summary.read_text(encoding="utf-8")))
        except ValueError:
            keys = set()
        unexpected = keys - EXPECTED_SUMMARY_KEYS
        if unexpected:
            findings.append(f"summary.json: unexpected keys {sorted(unexpected)!r}")
    return findings


def publish(input_dir: Path, public_dir: Path, label: str) -> int:
    findings = scan_output(input_dir)
    # The label is free text that lands on the landing page; scan it with
    # the same privacy rules before anything is written.
    findings.extend(str(f) for f in scan_lines("--label", label.splitlines(), set()))
    if findings:
        for finding in findings:
            print(f"publish: refused: {finding}", file=sys.stderr)
        return 1
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        index_text = build_index(input_dir, label, updated)
    except (ValueError, KeyError, TypeError) as exc:
        print(f"publish: refused: index build failed: {exc}", file=sys.stderr)
        return 1
    index_findings = [
        str(f) for f in scan_lines("index.html", index_text.splitlines(), set())
    ]
    if index_findings:
        for finding in index_findings:
            print(f"publish: refused: {finding}", file=sys.stderr)
        return 1
    data_dir = public_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_dir / "report.html", public_dir / "report.html")
    for name in DATA_FILES:
        shutil.copy2(input_dir / name, data_dir / name)
    (public_dir / "index.html").write_text(index_text, encoding="utf-8")
    print(f"publish: {len(REQUIRED)} files -> {public_dir} ({label})")
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
    args = parser.parse_args(argv)
    return publish(args.input, args.public, args.label)


if __name__ == "__main__":
    raise SystemExit(main())

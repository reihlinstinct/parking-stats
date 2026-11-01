"""Publish a local `p106-stats build` output directory into public/ (#4).

The raw municipal exports never leave the machine that runs `p106-stats`;
only its aggregate output directory reaches this publisher. Every file is
re-scanned with the privacy rules before anything is copied, and a single
finding aborts the publish with nothing written. GitHub Pages then serves
public/ as-is.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

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

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>סטטיסטיקת דיווחי חניה - ירושלים 106</title>
</head>
<body>
<h1>סטטיסטיקת דיווחי חניה - ירושלים 106</h1>
<p>כל הנתונים כאן מצטברים בלבד: ספירות לפי חודש, שבוע, קטגוריה, רחוב
ושכונה - ללא נתונים גולמיים, לוחיות רישוי או שמות.</p>
<p>מקור הנתונים: {label}. עודכן: {updated}.</p>
<ul>
<li><a href="report.html">הדוח המלא עם הגרפים</a></li>
<li><a href="data/summary.json">summary.json</a> (סיכום מכונה-קריא)</li>
<li>טבלאות CSV: <a href="data/monthly.csv">חודשי</a>,
<a href="data/categories.csv">קטגוריות</a>,
<a href="data/streets.csv">רחובות</a></li>
</ul>
</body>
</html>
"""


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
    # The label is free text that lands on the landing page; scan it and
    # the rendered index.html with the same privacy rules before writing.
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    index_text = INDEX_TEMPLATE.format(label=label, updated=updated)
    findings.extend(str(f) for f in scan_lines("--label", label.splitlines(), set()))
    findings.extend(
        str(f) for f in scan_lines("index.html", index_text.splitlines(), set())
    )
    if findings:
        for finding in findings:
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

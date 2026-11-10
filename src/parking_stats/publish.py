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
from . import i18n
from .analytics import add_analytics
from .charts import hbar_chart, vbar_chart
from .i18n import t, tn
from .privacy_scan import scan_lines

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
th, td { padding: .35rem .8rem; border-bottom: 1px solid #e2e8f0; text-align: start; }
th { color: #1e3a8a; font-size: .9rem; }
footer { text-align: center; color: #475569; font-size: .85rem; padding: 0 1rem 2rem; }
"""
CSS += i18n.PICKER_CSS

# Every visible word comes from the catalog (catalog.py) in the page's
# language; the data values (categories, streets, plates) stay as published.
PAGE = """<!DOCTYPE html>
<html {html_attrs}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{alternates}<style>{css}</style>
</head>
<body>
<header>
<h1>{title}</h1>
<p>{intro}</p>
{draft_notice}{picker}</header>
<main>
<div class="cards">{cards}</div>
<section><h2>{h_monthly}</h2>{monthly_chart}</section>
<section><h2>{h_categories}</h2>{categories_chart}</section>
<section><h2>{h_streets}</h2>{streets_chart}</section>
{plates_section}
<section><h2>{h_links}</h2>
<ul class="links">
<li><a href="report.html">{l_report}</a></li>
<li><a href="data/summary.json">summary.json</a> {l_summary_note}</li>
<li><a href="data/monthly.csv">{l_monthly}</a></li>
<li><a href="data/categories.csv">{l_categories}</a></li>
<li><a href="data/streets.csv">{l_streets}</a></li>
{plates_link}
{heatmap_link}
</ul>
</section>
</main>
<footer>{footer}</footer>
</body>
</html>
"""


def _read_counts(path: Path) -> list[tuple[str, int]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    return [(name, int(count)) for name, count in rows[1:] if len(rows) > 1]


def _median_text(days: float, lang: str | None = None) -> str:
    minutes = round(days * 24 * 60)
    if minutes < 60:
        return tn("duration.minutes", minutes, lang)
    hours = round(minutes / 60)
    if hours < 48:
        return tn("duration.hours", hours, lang)
    return tn("duration.days", round(days), lang)


MONTH_KEYS = tuple(f"month.{number:02d}" for number in range(1, 13))


def _month_year(iso_date: str, lang: str | None = None) -> str:
    """'2021-02-24' -> 'פברואר 2021' (also keeps digit chains out of the page)."""
    try:
        year, month, _ = iso_date.split("-", 2)
        return f"{t(MONTH_KEYS[int(month) - 1], lang)} {year}"
    except (ValueError, IndexError):
        return iso_date


def _card(number: str, caption: str) -> str:
    return (
        f'<div class="card"><div class="num">{html.escape(number)}</div>'
        f'<div class="cap">{html.escape(caption)}</div></div>'
    )


def _plates_section(plates: list[tuple[str, int]], lang: str | None = None) -> str:
    """Repeat-plates block: plate + report count only, inside the markers
    Kept as inert HTML comments after the plate rules were retired."""
    if not plates:
        return ""
    rows = "".join(
        '<tr><td dir="ltr">' + html.escape(plate) + "</td><td>"
        f"{tn('plates.count', count, lang)}</td></tr>"
        for plate, count in plates
    )
    return (
        f"<section><h2>{t('plates.title', lang)}</h2>\n"
        f"<p>{t('plates.intro', lang)}</p>\n"
        + "<!-- repeat-plates:begin -->\n"
        + f"<table><thead><tr><th>{t('plates.col.plate', lang)}</th>"
        + f"<th>{t('plates.col.reports', lang)}</th></tr></thead>"
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
    input_dir: Path,
    label: str | None,
    updated: str,
    *,
    heatmap_available: bool = False,
    lang: str | None = None,
) -> str:
    """Render the landing page from the aggregate output files.

    ``lang`` is a catalog language (the default language when None); a
    None ``label`` takes the catalog's default label in that language.
    """
    code = i18n.language(lang)["code"]
    if label is None:
        label = t("label.default", code)
    summary = json.loads((input_dir / "summary.json").read_text(encoding="utf-8"))
    monthly = _read_counts(input_dir / "monthly.csv")
    categories = _read_counts(input_dir / "categories.csv")
    streets = _read_counts(input_dir / "streets.csv")

    by_month = summary.get("by_month") or {}
    peak = (
        _month_year(max(by_month.items(), key=lambda item: item[1])[0] + "-01", code)
        if by_month
        else "-"
    )
    date_range = summary.get("date_range") or {}
    period = " - ".join(
        _month_year(str(date_range.get(edge, "")), code) for edge in ("first", "last")
    ).strip(" -")
    median_days = (summary.get("closure") or {}).get("median_days")

    cards = [
        _card(f"{summary['total_incidents']:,}", t("card.reports", code)),
        _card(period or "-", t("card.period", code)),
        _card(peak, t("card.peak", code)),
    ]
    if isinstance(median_days, (int, float)):
        cards.append(
            _card(_median_text(float(median_days), code), t("card.median", code))
        )

    plates = _read_plates(input_dir)
    plates_link = (
        f'<li><a href="data/plates.csv">{t("plates.title", code)}</a></li>'
        if plates
        else ""
    )
    heatmap_link = (
        f'<li><a href="{i18n.page_name("heatmap", code)}">'
        f"{t('index.link.heatmap', code)}</a></li>"
        if heatmap_available
        else ""
    )
    return PAGE.format(
        html_attrs=i18n.html_attrs(code),
        title=t("site.title", code),
        alternates=i18n.alternate_links("index"),
        css=CSS,
        intro=t("index.intro", code),
        draft_notice=i18n.draft_notice(code),
        picker=i18n.picker("index", code),
        cards="".join(cards),
        h_monthly=t("index.monthly", code),
        monthly_chart=vbar_chart(monthly[-12:], lang=code),
        h_categories=t("index.categories", code),
        categories_chart=hbar_chart(categories[:6], lang=code),
        h_streets=t("index.streets", code),
        streets_chart=hbar_chart(streets[:6], lang=code),
        plates_section=_plates_section(plates, code),
        h_links=t("index.links", code),
        l_report=t("index.link.report", code),
        l_summary_note=t("index.link.summary_note", code),
        l_monthly=t("index.link.monthly", code),
        l_categories=t("index.link.categories", code),
        l_streets=t("index.link.streets", code),
        plates_link=plates_link,
        heatmap_link=heatmap_link,
        footer=t(
            "index.footer",
            code,
            label=f"<bdi>{html.escape(label)}</bdi>",
            updated=html.escape(updated),
        ),
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
            str(f) for f in scan_lines("plates.csv", text.splitlines(), set())
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
    input_dir: Path, updated: str, geocoding: Path | None, basemap: Path | None
) -> dict[str, str] | None:
    """Render one heat map page per catalog language + heatmap.json (#1).

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
        lines = heatmap_mod.load_basemap(basemap) if basemap is not None else None
        files = {
            i18n.page_name("heatmap", code): heatmap_mod.build_heatmap_html(
                cells, unmapped, below, updated, basemap_lines=lines, lang=code
            )
            for code in i18n.codes()
        }
        files["heatmap.json"] = heatmap_mod.build_heatmap_json(
            cells, unmapped, below, updated
        )
        # First-party zoom/pan script, served same-origin under
        # script-src 'self' (#28); static content, no requests.
        files["data/map.js"] = heatmap_mod.MAP_JS
        return files
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"publish: heat map error: {exc}", file=sys.stderr)
        return None


def _page_language(name: str) -> str | None:
    """The catalog language of a generated page name, None for data files."""
    for code in i18n.codes():
        for base in ("index", "heatmap"):
            if name == i18n.page_name(base, code):
                return code
    return None


def publish(
    input_dir: Path,
    public_dir: Path,
    label: str | None,
    geocoding: Path | None = None,
    basemap: Path | None = None,
) -> int:
    """Publish behind the privacy gate: one page set per catalog language.

    ``label`` is shown on every language's landing page; None takes each
    language's default label from the catalog.
    """
    findings = scan_output(input_dir)
    # The label is free text that lands on the landing page; scan it with
    # the same privacy rules before anything is written.
    labels = (
        [label] if label is not None else [t("label.default", c) for c in i18n.codes()]
    )
    for text in labels:
        findings.extend(str(f) for f in scan_lines("--label", text.splitlines(), set()))
    if findings:
        for finding in findings:
            print(f"publish: refused: {finding}", file=sys.stderr)
        return 1
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    heatmap_files = _build_heatmap(input_dir, updated, geocoding, basemap)
    if heatmap_files is None:
        print("publish: refused: heat map build failed", file=sys.stderr)
        return 1
    index_pages: dict[str, str] = {}
    for code in i18n.codes():
        name = i18n.page_name("index", code)
        try:
            index_text = build_index(
                input_dir,
                label,
                updated,
                heatmap_available=bool(heatmap_files),
                lang=code,
            )
        except (ValueError, KeyError, TypeError) as exc:
            print(f"publish: refused: index build failed: {exc}", file=sys.stderr)
            return 1
        index_findings = [
            str(f) for f in scan_lines(name, index_text.splitlines(), set())
        ]
        if index_findings:
            for finding in index_findings:
                print(f"publish: refused: {finding}", file=sys.stderr)
            return 1
        index_pages[name] = add_analytics(index_text, lang=code)
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
        if name.startswith("data/"):
            target = public_dir / name
        if name.endswith(".html"):
            text = add_analytics(text, lang=_page_language(name))
        target.write_text(text, encoding="utf-8")
    stale_pages = [public_dir / i18n.page_name("heatmap", c) for c in i18n.codes()]
    for stale in (*stale_pages, data_dir / "heatmap.json", data_dir / "basemap.svg"):
        if not heatmap_files and stale.exists():
            stale.unlink()
    if heatmap_files and (data_dir / "basemap.svg").exists():
        # The basemap is inlined into heatmap.html since #28; a stale
        # standalone basemap.svg from an older build is removed.
        (data_dir / "basemap.svg").unlink()
    # report.html is generated upstream and only copied here; add the
    # counter idempotently so republishes keep it and the disclosure.
    report_text = (input_dir / "report.html").read_text(encoding="utf-8")
    (public_dir / "report.html").write_text(
        add_analytics(report_text), encoding="utf-8"
    )
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
    for name, text in index_pages.items():
        (public_dir / name).write_text(text, encoding="utf-8")
    shown = label if label is not None else labels[0]
    print(f"publish: {copied} files -> {public_dir} ({shown})")
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
        default=None,
        help="data-source label shown on every language's landing page "
        "(default: the catalog's label in each language)",
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
    parser.add_argument(
        "--basemap",
        type=Path,
        default=(
            Path(os.environ["PARKING_STATS_BASEMAP"])
            if os.environ.get("PARKING_STATS_BASEMAP")
            else None
        ),
        help="vendored public OSM street-line JSON (scripts/fetch_basemap.py); "
        "adds a static basemap under the heat layer",
    )
    args = parser.parse_args(argv)
    return publish(args.input, args.public, args.label, args.geocoding, args.basemap)


if __name__ == "__main__":
    raise SystemExit(main())

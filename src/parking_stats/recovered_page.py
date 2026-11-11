"""Render the public landing page from the two validated aggregate files.

Input is only ``recovered-summary.json`` (strict aggregate schema) and
``source-freshness.json`` (strict freshness schema). Every label is escaped and
must already be in the reviewed public label lists, so the page carries counts
and reviewed category/street names only: no case numbers, plates or free text.
"""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import i18n
from .analytics import add_analytics
from .i18n import t
from .privacy_scan import scan_lines
from .private_builder import Reader
from .source_refresh import freshness
from .union import UnionError, validate_aggregate

UNKNOWN = "other_or_unknown"
TOP_ROWS = 12
MONTH_ROWS = 12

STYLE = """*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#152536;font:17px/1.6 Arial,sans-serif}header{background:#183b58;color:white;padding:40px 24px}header>div,main{max-width:1000px;margin:auto}h1{font-size:clamp(28px,5vw,40px);line-height:1.2;margin:8px 0 16px}header p{max-width:760px;margin:8px 0}main{padding:24px 16px}h2{font-size:22px;margin:0 0 16px}.tag{font-size:14px;letter-spacing:.04em}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}.card,section{background:white;border:1px solid #d9e2ea;border-radius:12px;padding:22px}.num{font-size:36px;font-weight:bold;color:#183b58}.caption{font-size:15px}.notice{border-right:5px solid #bd7618;background:#fffbeb;margin-bottom:24px;padding:18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}table{border-collapse:collapse;width:100%}th,td{text-align:right;padding:9px 0;border-bottom:1px solid #e6ebf0}tbody th{font-weight:normal}td{text-align:left;font-variant-numeric:tabular-nums}a{color:#145d94}footer{padding:24px 0;font-size:14px;color:#46596b}ul{padding-right:22px}section{margin-bottom:20px}@media(max-width:620px){.cards,.grid{grid-template-columns:1fr}.card{padding:16px}.num{font-size:32px}header{padding:28px 18px}section{padding:18px}body{font-size:16px}}"""

# Shows each timestamp in the visitor's own time zone; the UTC text stays as the
# no-script fallback. No network, no storage.
LOCALIZE = (
    "<script>document.querySelectorAll('time[datetime]').forEach(function(e){"
    "var d=new Date(e.getAttribute('datetime'));if(!isNaN(d)){"
    "e.textContent=d.toLocaleString('@@LANG@@',{dateStyle:'short',timeStyle:'short'})}"
    "})</script>"
)


def _num(value: int) -> str:
    return f"{value:,}"


def _instant(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    iso = html.escape(parsed.isoformat(timespec="minutes"))
    shown = parsed.strftime("%Y-%m-%d %H:%M") + (
        " UTC" if parsed.utcoffset() == timedelta(0) else ""
    )
    return f'<time datetime="{iso}"><bdi>{html.escape(shown)}</bdi></time>'


def _table(counts: dict[str, int], limit: int, lang: str) -> str:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] == UNKNOWN, kv[0]))
    rows = "".join(
        f'<tr><th scope="row">'
        f"{t('recovered.unknown', lang) if k == UNKNOWN else html.escape(k)}"
        f"</th><td>{_num(v)}</td></tr>"
        for k, v in ranked[:limit]
    )
    head = f"<th>{t('recovered.group', lang)}</th><th>{t('recovered.cases', lang)}</th>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def render_body(
    summary: dict[str, Any], fresh: dict[str, Any], lang: str | None = None
) -> str:
    code = i18n.language(lang)["code"]

    def tr(key: str, **params: object) -> str:
        return t(key, code, **params)

    months = sorted(summary["by_month"].items(), reverse=True)[:MONTH_ROWS]
    month_rows = "".join(
        f'<tr><th scope="row"><bdi>{html.escape(m)}</bdi></th><td>{_num(v)}</td></tr>'
        for m, v in months
    )
    city = fresh["city_events_snapshot_at"]
    city_line = (
        f"<p>{tr('recovered.city_events', when=_instant(city))}</p>"
        if city
        else f"<p>{tr('recovered.no_city_events')}</p>"
    )
    dates = (
        "<p>"
        + tr(
            "recovered.dates",
            first=html.escape(summary["from_date"]),
            last=html.escape(summary["through"]),
            missing=_num(summary["missing_opening_date"]),
        )
        + "</p>"
        if summary["from_date"]
        else f"<p>{tr('recovered.no_dates')}</p>"
    )
    cards = "".join(
        f'<div class="card"><div class="num">{_num(n)}</div>'
        f'<div class="caption">{tr(key)}</div></div>'
        for n, key in (
            (summary["case_identifiers"], "recovered.card_total"),
            (summary["unresolved_rows"], "recovered.card_unresolved"),
            (summary["missing_opening_date"], "recovered.card_missing"),
        )
    )
    body = (
        f"<!doctype html><html {i18n.html_attrs(code)}><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{tr('recovered.title')}</title>"
        f"<style>\n{STYLE}\n</style></head><body>"
        f'<header><div><div class="tag">{tr("recovered.tag")}</div>'
        f"<h1>{tr('recovered.h1')}</h1><p>{tr('recovered.lede')}</p>"
        f"<p>{tr('recovered.ledger_refreshed', when=_instant(fresh['ledger_observed_at']))}"
        "</p></div></header><main>"
        f'<div class="cards">{cards}</div>'
        f'<div class="notice">{tr("recovered.notice")}</div>'
        f"<section><h2>{tr('recovered.sources_h')}</h2>"
        f"<p>{tr('recovered.sources_p', snapshot=_instant(fresh['email_snapshot_at']))}</p>"
        f"{city_line}{dates}</section>"
        f'<div class="grid"><section><h2>{tr("recovered.categories_h")}</h2>'
        f"{_table(summary['by_category'], TOP_ROWS, code)}</section>"
        f"<section><h2>{tr('recovered.streets_h')}</h2>"
        f"{_table(summary['by_street'], TOP_ROWS, code)}</section></div>"
        f"<section><h2>{tr('recovered.monthly_h', count=MONTH_ROWS)}</h2>"
        f"<p>{tr('recovered.monthly_p')}</p>"
        f"<table><thead><tr><th>{tr('recovered.group')}</th><th>{tr('recovered.cases')}"
        f"</th></tr></thead><tbody>{month_rows}</tbody></table></section>"
        f"<section><h2>{tr('recovered.data_h')}</h2><ul>"
        f'<li><a href="data/recovered-summary.json">{tr("recovered.link_summary")}</a></li>'
        f'<li><a href="data/source-freshness.json">{tr("recovered.link_freshness")}</a></li>'
        f'<li><a href="historical.html">{tr("recovered.link_archive")}</a>'
        f"{tr('recovered.link_archive_note')}</li></ul>"
        f"<p>{tr('recovered.labels_note')}</p></section>"
        f"<footer>{tr('recovered.footer')}</footer></main>"
        f"{LOCALIZE.replace('@@LANG@@', code)}</body></html>"
    )
    return body


def render(
    summary: dict[str, Any], fresh: dict[str, Any], lang: str | None = None
) -> str:
    """The page with the anonymous visit counter, as published."""
    code = i18n.language(lang)["code"]
    return add_analytics(render_body(summary, fresh, code), lang=code)


def build_page(public: Path, config: Path) -> str:
    """Validate both inputs against the reviewed labels, then render."""
    spec = Reader(config.parent).obj(config.name)
    summary = Reader(public).obj("public/data/recovered-summary.json")
    fresh = Reader(public).obj("public/data/source-freshness.json")
    validate_aggregate(
        summary, streets=set(spec["streets"]), categories=set(spec["categories"])
    )
    freshness(fresh)
    # Scan the page before the beacon is added, as publish.py does: the public
    # counter token is a long hex run the phone rule would flag.
    if scan_lines("index.html", render_body(summary, fresh).splitlines(), set()):
        raise UnionError("page_privacy")
    return render(summary, fresh)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recovered_page")
    parser.add_argument("--public", type=Path, required=True, help="repo checkout")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        page = build_page(args.public, args.config)
        (args.public / "public/index.html").write_text(page, encoding="utf-8")
    except Exception as error:
        code = error.args[0] if isinstance(error, UnionError) else "unexpected_error"
        print(f"recovered_page_failed code={code}", file=sys.stderr)
        return 1
    print("page_written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

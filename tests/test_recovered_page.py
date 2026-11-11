"""The landing page renders from validated aggregates only."""

import json
import tempfile
import unittest
from pathlib import Path

from parking_stats import i18n, recovered_page
from parking_stats.privacy_scan import scan_lines
from parking_stats.union import UnionError

STAMP = "2026-11-11T10:00:00+00:00"
SUMMARY = dict(
    schema_version=1,
    basis="recovered_case_identifiers_not_certified",
    case_identifiers=1150,
    unresolved_rows=59,
    missing_opening_date=29,
    from_date="2026-10-03",
    through="2026-11-11",
    by_month={"2026-10": 700, "2026-11": 421},
    by_street={"alpha": 1000, "other_or_unknown": 150},
    by_category={"parking": 900, "other_or_unknown": 250},
)
FRESH = dict(
    schema_version=2,
    refresh_mode="private_checkout_pull",
    ledger_observed_at=STAMP,
    email_snapshot_at="2026-11-04T10:47:15+02:00",
    email_refresh_mode="historical_snapshot_not_live",
    email_stale=True,
    city_events_snapshot_at="2026-11-11T12:16:06+02:00",
    live_city_status=False,
)


class RenderTests(unittest.TestCase):
    def test_counts_labels_and_honest_wording(self):
        page = recovered_page.render(SUMMARY, FRESH)
        for text in ("1,150", "59", "29", "alpha", "parking", "2026-11"):
            self.assertIn(text, page)
        self.assertIn("אחר / לא ידוע", page)
        self.assertIn("ללא שיוך מאומת", page)
        self.assertIn("היסטורי בלבד", page)
        self.assertIn("לא בדיקת סטטוס חיה", page)
        self.assertNotIn("ידני", page)
        self.assertIn(i18n.html_attrs(i18n.default_code()), page)
        self.assertIn("cloudflareinsights.com/beacon", page)
        body = recovered_page.render_body(SUMMARY, FRESH)
        self.assertEqual(scan_lines("index.html", body.splitlines(), set()), [])

    def test_no_city_snapshot_and_no_dates(self):
        empty = dict(
            SUMMARY,
            case_identifiers=0,
            missing_opening_date=0,
            from_date=None,
            through=None,
            by_month={},
            by_street={},
            by_category={},
        )
        page = recovered_page.render(empty, dict(FRESH, city_events_snapshot_at=None))
        self.assertIn("טרם נשמרו אישורי פתיחה", page)
        self.assertIn("אין עדיין תאריכי פתיחה", page)

    def test_labels_are_escaped(self):
        hostile = dict(SUMMARY, by_street={"<b>x</b>": 1150})
        page = recovered_page.render(hostile, FRESH)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_french_catalog_renders(self):
        other = [c for c in i18n.codes() if c != i18n.default_code()][0]
        page = recovered_page.render(SUMMARY, FRESH, other)
        self.assertIn(i18n.html_attrs(other), page)
        self.assertIn("sans rattachement vérifié", page)

    def test_build_page_validates_inputs_against_reviewed_labels(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "public/data").mkdir(parents=True)
            (root / "config").mkdir()
            config = root / "config/parking-source.json"
            config.write_text(
                json.dumps(dict(streets=["alpha"], categories=["parking"]))
            )
            (root / "public/data/recovered-summary.json").write_text(
                json.dumps(SUMMARY)
            )
            (root / "public/data/source-freshness.json").write_text(json.dumps(FRESH))
            self.assertIn("1,150", recovered_page.build_page(root, config))
            (root / "public/index.html").write_text("old")
            self.assertEqual(
                recovered_page.main(["--public", str(root), "--config", str(config)]),
                0,
            )
            self.assertIn("1,150", (root / "public/index.html").read_text())
            # An unreviewed label is rejected and the old page is untouched.
            config.write_text(json.dumps(dict(streets=["beta"], categories=[])))
            (root / "public/index.html").write_text("old")
            with self.assertRaises(UnionError):
                recovered_page.build_page(root, config)
            self.assertEqual(
                recovered_page.main(["--public", str(root), "--config", str(config)]),
                1,
            )
            self.assertEqual((root / "public/index.html").read_text(), "old")


if __name__ == "__main__":
    unittest.main()

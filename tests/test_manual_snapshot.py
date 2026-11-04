"""Validate committed public snapshot without any private source fixtures."""

import json
import unittest
from pathlib import Path

from parking_stats.union import validate_aggregate

ROOT = Path(__file__).resolve().parents[1]


class ManualSnapshotTests(unittest.TestCase):
    def test_strict_snapshot(self):
        payload = json.loads((ROOT / "public/data/recovered-summary.json").read_text())
        validate_aggregate(
            payload,
            streets=set(payload["by_street"]) - {"other_or_unknown"},
            categories=set(payload["by_category"]) - {"other_or_unknown"},
        )
        self.assertEqual(payload["case_identifiers"], 1018)
        self.assertEqual(payload["missing_opening_date"], 25)
        self.assertEqual(payload["unresolved_rows"], 7)

    def test_manual_labels_and_historical_preservation(self):
        page = (ROOT / "public/index.html").read_text()
        metadata = json.loads((ROOT / "public/data/manual-refresh.json").read_text())
        self.assertEqual(metadata["refresh_mode"], "manual")
        self.assertIn("לא אוטומטי", page)
        self.assertIn("historical.html", page)
        self.assertIn("לא נתונים חיים", (ROOT / "public/historical.html").read_text())

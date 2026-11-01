"""Tests for the publish pipeline: copies behind the privacy gate (#4)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from parking_stats.publish import DATA_FILES, publish

PLATE = "12" + "-345-" + "67"

FILES = {
    "summary.json": '{"total_incidents": 13, "by_month": {"2026-05": 4}}\n',
    "monthly.csv": "month,reports\n2026-05,4\n",
    "categories.csv": "category,reports\nחניה אסורה,9\n",
    "streets.csv": "street,reports\nרחוב הדוגמה,8\n",
    "report.html": "<html dir=rtl><body>דוח</body></html>\n",
}


def make_input(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


class PublishTest(unittest.TestCase):
    def test_publish_copies_everything_and_writes_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            code = publish(make_input(FILES), public, "נתוני דוגמה סינתטיים")
            self.assertEqual(code, 0)
            self.assertTrue((public / "report.html").exists())
            for name in DATA_FILES:
                self.assertEqual(
                    (public / "data" / name).read_text(encoding="utf-8"), FILES[name]
                )
            index = (public / "index.html").read_text(encoding="utf-8")
            self.assertIn("נתוני דוגמה סינתטיים", index)
            self.assertIn('href="report.html"', index)
            self.assertIn('href="data/summary.json"', index)

    def test_missing_input_file_is_refused(self):
        files = dict(FILES)
        del files["report.html"]
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(files), public, "x"), 1)
            self.assertFalse(public.exists())

    def test_unexpected_csv_columns_are_refused(self):
        files = dict(FILES)
        files["monthly.csv"] = "month," + "description" + ",reports\n2026-05,x,4\n"
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(files), public, "x"), 1)
            self.assertFalse(public.exists())

    def test_unexpected_summary_keys_are_refused(self):
        files = dict(FILES)
        files["summary.json"] = '{"total_incidents": 1, "notes": "x"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(files), public, "x"), 1)
            self.assertFalse(public.exists())

    def test_label_with_plate_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            code = publish(make_input(FILES), public, f"רכב {PLATE}")
            self.assertEqual(code, 1)
            self.assertFalse(public.exists())

    def test_plate_in_any_input_aborts_with_nothing_copied(self):
        files = dict(FILES)
        files["streets.csv"] = f"street,reports\nרחוב {PLATE},3\n"
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(files), public, "x"), 1)
            self.assertFalse((public / "data").exists())
            self.assertFalse((public / "index.html").exists())


if __name__ == "__main__":
    unittest.main()

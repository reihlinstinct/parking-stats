"""Tests for the publish pipeline: copies behind the privacy gate (#4)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from parking_stats.publish import DATA_FILES, check_shape, publish

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


PLATE8 = "123" + "-45-" + "678"
PLATES_CSV = "plate,reports\n" + PLATE8 + ",4\n" + PLATE + ",3\n"


class RepeatPlatesCountTest(unittest.TestCase):
    """#14: repeat_plates_count must be an int >= 0 when present."""

    def _code(self, value: object) -> int:
        files = dict(FILES)
        files["summary.json"] = (
            '{"total_incidents": 13, "repeat_plates_count": '
            + json.dumps(value)
            + "}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            return publish(make_input(files), public, "x")

    def test_absent_key_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(dict(FILES)), public, "x"), 0)

    def test_zero_and_positive_int_are_accepted(self):
        self.assertEqual(self._code(0), 0)
        self.assertEqual(self._code(17), 0)

    def test_non_int_is_refused(self):
        for bad in ("3", 2.5, [1], {"n": 1}):
            self.assertEqual(self._code(bad), 1, bad)

    def test_bool_is_refused(self):
        self.assertEqual(self._code(True), 1)
        self.assertEqual(self._code(False), 1)

    def test_negative_is_refused(self):
        self.assertEqual(self._code(-1), 1)

    def test_refusal_copies_nothing(self):
        files = dict(FILES)
        files["summary.json"] = '{"total_incidents": 1, "repeat_plates_count": -2}\n'
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            self.assertEqual(publish(make_input(files), public, "x"), 1)
            self.assertFalse(public.exists())


class NonObjectSummaryTest(unittest.TestCase):
    """#19: a valid-JSON non-object summary.json is a finding, not a crash."""

    def _code(self, raw: str) -> int:
        files = dict(FILES)
        files["summary.json"] = raw
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp) / "public"
            return publish(make_input(files), public, "x")

    def test_array_summary_is_refused_without_raising(self):
        self.assertEqual(self._code("[1, 2]\n"), 1)

    def test_string_summary_is_refused_without_raising(self):
        self.assertEqual(self._code('"just a string"\n'), 1)

    def test_null_summary_is_refused_without_raising(self):
        self.assertEqual(self._code("null\n"), 1)

    def test_invalid_json_still_refused(self):
        self.assertEqual(self._code("{not json\n"), 1)


class PublishPlatesTest(unittest.TestCase):
    def test_publish_with_plates_csv_copies_and_renders_section(self):
        input_dir = make_input({**FILES, "plates.csv": PLATES_CSV})
        public = Path(tempfile.mkdtemp()) / "public"
        self.assertEqual(publish(input_dir, public, "בדיקה"), 0)
        self.assertEqual(
            (public / "data" / "plates.csv").read_text(encoding="utf-8"), PLATES_CSV
        )
        index = (public / "index.html").read_text(encoding="utf-8")
        self.assertIn("רכבים חוזרים", index)
        self.assertIn(PLATE8, index)
        self.assertIn("repeat-plates:begin", index)

    def test_publish_removes_stale_plates_csv(self):
        input_dir = make_input(FILES)
        public = Path(tempfile.mkdtemp()) / "public"
        (public / "data").mkdir(parents=True)
        (public / "data" / "plates.csv").write_text(PLATES_CSV, encoding="utf-8")
        self.assertEqual(publish(input_dir, public, "בדיקה"), 0)
        self.assertFalse((public / "data" / "plates.csv").exists())
        index = (public / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("רכבים חוזרים", index)

    def test_publish_refuses_wrong_plates_columns(self):
        input_dir = make_input({**FILES, "plates.csv": "street,reports\nx,1\n"})
        public = Path(tempfile.mkdtemp()) / "public"
        self.assertEqual(publish(input_dir, public, "בדיקה"), 1)

    def test_publish_refuses_noncanonical_plate_row(self):
        bad = "plate,reports\n" + "1234" + "5678" + ",4\n"
        input_dir = make_input({**FILES, "plates.csv": bad})
        public = Path(tempfile.mkdtemp()) / "public"
        self.assertEqual(publish(input_dir, public, "בדיקה"), 1)

    def test_publish_refuses_plate_in_label_even_with_section(self):
        input_dir = make_input({**FILES, "plates.csv": PLATES_CSV})
        public = Path(tempfile.mkdtemp()) / "public"
        self.assertEqual(publish(input_dir, public, "תווית " + PLATE), 1)

    def test_publish_refuses_count_below_repeat_threshold(self):
        low = "plate,reports\n" + PLATE8 + ",2\n"
        input_dir = make_input({**FILES, "plates.csv": low})
        public = Path(tempfile.mkdtemp()) / "public"
        self.assertEqual(publish(input_dir, public, "בדיקה"), 1)


class CommittedPlatesTest(unittest.TestCase):
    """CI gate: the committed published data keeps its approved shape."""

    REPO_DATA = Path(__file__).resolve().parent.parent / "public" / "data"

    def test_committed_data_shape(self):
        if not (self.REPO_DATA / "plates.csv").exists():
            self.skipTest("no committed plates.csv yet")
        self.assertEqual(check_shape(self.REPO_DATA), [])

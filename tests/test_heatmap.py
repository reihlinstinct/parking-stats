"""Heat-map payload and page tests (#1)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from parking_stats import heatmap
from parking_stats.privacy_scan import scan_lines
from parking_stats.publish import publish


class LoadMappingTest(unittest.TestCase):
    def test_committed_mapping_loads(self):
        mapping = heatmap.load_mapping()
        self.assertGreaterEqual(len(mapping), 1)
        for key, (lat, lng) in mapping.items():
            self.assertTrue(key.strip())
            self.assertTrue(29.0 <= lat <= 34.0)
            self.assertTrue(33.0 <= lng <= 36.5)

    def _write(self, payload):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(payload, tmp)
        tmp.close()
        return Path(tmp.name)

    def test_rejects_bad_entry_shape(self):
        path = self._write({"x 1": [31.7]})
        with self.assertRaises(ValueError):
            heatmap.load_mapping(path)

    def test_rejects_out_of_bounds(self):
        path = self._write({"x 1": [51.5, 0.0]})
        with self.assertRaises(ValueError):
            heatmap.load_mapping(path)

    def test_rejects_non_object(self):
        path = self._write([1, 2])
        with self.assertRaises(ValueError):
            heatmap.load_mapping(path)


class BuildPointsTest(unittest.TestCase):
    MAPPING = {
        "a 1": (31.77004, 35.21204),
        "a 2": (31.77002, 35.21201),  # rounds to the same cell as a 1
        "b 5": (31.7654, 35.2092),
    }

    def test_below_threshold_never_shown(self):
        points, below, unmapped = heatmap.build_points({"a 1": 2}, self.MAPPING)
        self.assertEqual(points, [])
        self.assertEqual((below, unmapped), (1, 0))

    def test_threshold_boundary_shown(self):
        points, below, _ = heatmap.build_points({"a 1": 3}, self.MAPPING)
        self.assertEqual(len(points), 1)
        self.assertEqual(below, 0)
        self.assertEqual(points[0]["n"], 3)

    def test_rounding_aggregates_counts(self):
        points, _, _ = heatmap.build_points({"a 1": 5, "a 2": 4}, self.MAPPING)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["n"], 9)
        self.assertEqual(points[0]["lat"], round(31.77004, 3))
        self.assertEqual(points[0]["lng"], round(35.21204, 3))

    def test_unmapped_counted_not_shown(self):
        points, _, unmapped = heatmap.build_points(
            {"missing 9": 10, "b 5": 7}, self.MAPPING
        )
        self.assertEqual(unmapped, 1)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["label"], "b 5")

    def test_sorted_by_count_desc(self):
        points, _, _ = heatmap.build_points({"a 1": 3, "b 5": 9}, self.MAPPING)
        self.assertEqual([p["label"] for p in points], ["b 5", "a 1"])

    def test_bad_count_rejected(self):
        with self.assertRaises(ValueError):
            heatmap.build_points({"a 1": "3"}, self.MAPPING)
        with self.assertRaises(ValueError):
            heatmap.build_points({"a 1": True}, self.MAPPING)
        with self.assertRaises(ValueError):
            heatmap.build_points({"a 1": -1}, self.MAPPING)

    def test_point_keys_limited(self):
        points, _, _ = heatmap.build_points({"a 1": 5}, self.MAPPING)
        self.assertEqual(set(points[0]), {"lat", "lng", "n", "label"})


class PayloadAndPageTest(unittest.TestCase):
    def setUp(self):
        points, _, unmapped = heatmap.build_points(
            {"a 1": 5, "b 5": 3, "c 9": 2},
            heatmap.load_mapping.__wrapped__()
            if False
            else {
                "a 1": (31.77004, 35.21204),
                "b 5": (31.76540, 35.20920),
            },
        )
        self.points = points
        self.payload = heatmap.build_heatmap_json(
            points, unmapped, "2026-11-01 15:00 UTC"
        )
        self.page = heatmap.build_heatmap_html()

    def test_payload_shape_and_threshold(self):
        data = json.loads(self.payload)
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["threshold"], 3)
        self.assertEqual(data["rounding_decimals"], 3)
        self.assertTrue(all(p["n"] >= 3 for p in data["points"]))
        self.assertEqual(
            set(data),
            {
                "schema_version",
                "threshold",
                "rounding_decimals",
                "updated",
                "unmapped_spots",
                "points",
            },
        )

    def test_payload_passes_privacy_scan(self):
        self.assertEqual(
            scan_lines("heatmap.json", self.payload.splitlines(), set()), []
        )

    def test_page_offline_build_external_only_at_runtime(self):
        self.assertIn("https://unpkg.com/leaflet@", self.page)
        self.assertIn("leaflet.heat", self.page)
        self.assertIn("https://tile.openstreetmap.org/{z}/{x}/{y}.png", self.page)
        self.assertIn("openstreetmap.org/copyright", self.page)
        self.assertIn("data/heatmap.json", self.page)

    def test_page_passes_privacy_scan(self):
        self.assertEqual(scan_lines("heatmap.html", self.page.splitlines(), set()), [])


class PublishWiringTest(unittest.TestCase):
    def _input_dir(self, hotspots):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "total_incidents": 3,
                    "date_range": {"first": "2026-10-01", "last": "2026-10-31"},
                    "by_month": {"2026-10": 3},
                    "by_week": {},
                    "by_category": {},
                    "by_street": {},
                    "by_status": {},
                    "by_neighborhood": {},
                    "hotspots": hotspots,
                    "closure": {},
                    "inputs": ["x.json"],
                    "duplicates_skipped": 0,
                    "repeat_plates_count": 0,
                }
            ),
            encoding="utf-8",
        )
        for name, cols in (
            ("monthly.csv", "month,reports\n2026-10,3\n"),
            ("categories.csv", "category,reports\nx,3\n"),
            ("streets.csv", "street,reports\na,3\n"),
        ):
            (tmp / name).write_text(cols, encoding="utf-8")
        (tmp / "report.html").write_text("<html></html>", encoding="utf-8")
        return tmp

    def test_publish_writes_heatmap_files(self):
        input_dir = self._input_dir({"חרלפ 17": 5})
        public = Path(tempfile.mkdtemp())
        self.assertEqual(publish(input_dir, public, "label"), 0)
        self.assertTrue((public / "heatmap.html").exists())
        data = json.loads(
            (public / "data" / "heatmap.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(data["points"]), 1)
        index = (public / "index.html").read_text(encoding="utf-8")
        self.assertIn("heatmap.html", index)

    def test_publish_without_hotspots_removes_stale(self):
        input_dir = self._input_dir({})
        public = Path(tempfile.mkdtemp())
        (public / "data").mkdir()
        (public / "heatmap.html").write_text("stale", encoding="utf-8")
        (public / "data" / "heatmap.json").write_text("stale", encoding="utf-8")
        self.assertEqual(publish(input_dir, public, "label"), 0)
        self.assertFalse((public / "heatmap.html").exists())
        self.assertFalse((public / "data" / "heatmap.json").exists())
        index = (public / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("heatmap.html", index)

    def test_unmapped_hotspot_skipped_and_counted(self):
        input_dir = self._input_dir({"רחוב לא ממופה 99": 9})
        public = Path(tempfile.mkdtemp())
        self.assertEqual(publish(input_dir, public, "label"), 0)
        data = json.loads(
            (public / "data" / "heatmap.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["points"], [])
        self.assertEqual(data["unmapped_spots"], 1)


if __name__ == "__main__":
    unittest.main()

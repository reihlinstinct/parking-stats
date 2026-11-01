"""Heat map tests (#1): street-level, floored, rounded, static output."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from parking_stats import heatmap


class LoadMappingTest(unittest.TestCase):
    def _write(self, payload: object) -> Path:
        path = Path(self.tmp.name) / "mapping.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_valid_mapping_loads(self) -> None:
        path = self._write({"א": [31.77, 35.21]})
        self.assertEqual(heatmap.load_mapping(path), {"א": (31.77, 35.21)})

    def test_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write([1, 2]))

    def test_rejects_bad_entry(self) -> None:
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write({"א": [31.77]}))
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write({"א": [31.77, "x"]}))
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write({"א": [31.77, True]}))
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write({" ": [31.77, 35.21]}))

    def test_rejects_out_of_bounds(self) -> None:
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write({"א": [10.5, 35.21]}))
        with self.assertRaises(ValueError):
            heatmap.load_mapping(self._write({"א": [31.77, 60.5]}))


class BuildCellsTest(unittest.TestCase):
    MAPPING = {
        "א": (31.7704, 35.2104),
        "ב": (31.7702, 35.2102),  # rounds to the same cell as א
        "ג": (31.7654, 35.2092),
    }

    def test_below_threshold_never_shown(self) -> None:
        cells, below, unmapped = heatmap.build_cells(
            {"א": heatmap.MIN_STREET_REPORTS - 1}, self.MAPPING
        )
        self.assertEqual((cells, below, unmapped), ([], 1, 0))

    def test_threshold_is_above_three(self) -> None:
        self.assertGreater(heatmap.MIN_STREET_REPORTS, 3)
        cells, _below, _unmapped = heatmap.build_cells(
            {"א": heatmap.MIN_STREET_REPORTS}, self.MAPPING
        )
        self.assertEqual(len(cells), 1)

    def test_label_is_street_name_only(self) -> None:
        cells, _b, _u = heatmap.build_cells({"א": 9, "ג": 5}, self.MAPPING)
        labels = [c["label"] for c in cells]
        self.assertEqual(sorted(labels), ["א", "ג"])
        for label in labels:
            self.assertNotRegex(label, r"\d")  # no house numbers in labels

    def test_coordinates_rounded(self) -> None:
        cells, _b, _u = heatmap.build_cells({"ג": 5}, self.MAPPING)
        self.assertEqual(cells[0]["lat"], round(31.7654, 3))
        self.assertEqual(cells[0]["lng"], round(35.2092, 3))

    def test_same_cell_merges_streets(self) -> None:
        cells, _b, _u = heatmap.build_cells({"א": 4, "ב": 6}, self.MAPPING)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["n"], 10)
        self.assertEqual(cells[0]["label"], "א / ב")

    def test_unmapped_counted_never_shown(self) -> None:
        cells, below, unmapped = heatmap.build_cells({"ד": 9}, self.MAPPING)
        self.assertEqual((cells, below, unmapped), ([], 0, 1))

    def test_bad_count_rejected(self) -> None:
        for bad in (-1, 2.5, True, "4"):
            with self.assertRaises(ValueError):
                heatmap.build_cells({"א": bad}, self.MAPPING)  # type: ignore[dict-item]

    def test_sorted_by_count_descending(self) -> None:
        cells, _b, _u = heatmap.build_cells({"א": 4, "ג": 9}, self.MAPPING)
        self.assertEqual([c["n"] for c in cells], [9, 4])


class JsonTest(unittest.TestCase):
    def test_payload_shape(self) -> None:
        cells, _b, unmapped = heatmap.build_cells({"א": 9}, BuildCellsTest.MAPPING)
        payload = json.loads(heatmap.build_heatmap_json(cells, unmapped, 0, "now"))
        self.assertEqual(payload["aggregation"], "street")
        self.assertEqual(payload["threshold"], heatmap.MIN_STREET_REPORTS)
        self.assertEqual(payload["rounding_decimals"], heatmap.COORD_DECIMALS)
        self.assertEqual(payload["unmapped_streets"], unmapped)
        self.assertEqual(payload["cells"], cells)


class HtmlTest(unittest.TestCase):
    def _html(self, streets: dict[str, int]) -> str:
        mapping = dict(BuildCellsTest.MAPPING)
        mapping['x & <y> "z"'] = (31.7655, 35.2093)
        cells, _b, unmapped = heatmap.build_cells(streets, mapping)
        return heatmap.build_heatmap_html(cells, unmapped, 0, "2026-11-01 17:00 UTC")

    def test_static_page_no_external_resources(self) -> None:
        page = self._html({"א": 9})
        for forbidden in ("unpkg", "openstreetmap", "http://", "https://"):
            self.assertNotIn(forbidden, page)
        # The only script is the first-party zoom/pan helper, same-origin.
        self.assertEqual(page.count("<script"), 1)
        self.assertIn('<script src="data/map.js" defer></script>', page)
        self.assertIn("script-src 'self'", page)
        self.assertNotIn("script-src 'unsafe-inline'", page)
        self.assertIn("Content-Security-Policy", page)

    def test_street_name_escaped(self) -> None:
        page = self._html({'x & <y> "z"': 5})
        self.assertNotIn("<y>", page)
        self.assertIn("x &amp; &lt;y&gt; &quot;z&quot;", page)

    def test_labels_have_no_house_numbers(self) -> None:
        cells, _b, _u = heatmap.build_cells({"א": 9, "ג": 4}, BuildCellsTest.MAPPING)
        page = heatmap.build_heatmap_html(cells, 0, 0, "now")
        for cell in cells:
            self.assertNotRegex(cell["label"], r"\d")
        self.assertIn("<table>", page)

    def test_empty_cells_render_note(self) -> None:
        page = heatmap.build_heatmap_html([], 3, 0, "now")
        self.assertIn("3", page)  # unmapped note
        self.assertNotIn("<table>", page)


class NitFixes18Test(unittest.TestCase):
    """#18: below-threshold count surfaced, singular grammar, label split."""

    MAPPING = BuildCellsTest.MAPPING

    def test_json_carries_below_threshold_count(self) -> None:
        cells, below, unmapped = heatmap.build_cells(
            {"א": 9, "ג": 2, "ד": 1}, self.MAPPING
        )
        payload = json.loads(heatmap.build_heatmap_json(cells, unmapped, below, "now"))
        self.assertEqual(below, 2)
        self.assertEqual(payload["below_threshold_streets"], 2)

    def test_singular_grammar_in_notes(self) -> None:
        page = heatmap.build_heatmap_html([], 1, 1, "now")
        self.assertIn("רחוב נוסף אחד מעל הסף ללא מיפוי.", page)
        self.assertIn("רחוב נוסף אחד מתחת לסף ולכן אינו מוצג.", page)
        self.assertNotIn("1 רחובות", page)

    def test_plural_grammar_in_notes(self) -> None:
        page = heatmap.build_heatmap_html([], 3, 2, "now")
        self.assertIn("3 רחובות נוספים מעל הסף ללא מיפוי.", page)
        # #28: plural verb, pinned full-text.
        self.assertIn("2 רחובות נוספים מתחת לסף ולכן אינם מוצגים.", page)
        self.assertNotIn("אינו מוצג", page.replace("אינו מוצג.", ""))

    def test_street_name_containing_separator_survives_merge(self) -> None:
        tricky = "רחוב א / ב"
        mapping = dict(self.MAPPING)
        mapping[tricky] = (31.7704, 35.2104)  # same cell as א
        cells, _b, _u = heatmap.build_cells({"א": 4, tricky: 5}, mapping)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["n"], 9)
        self.assertEqual(cells[0]["label"], f"א / {tricky}")


class PublishWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.input_dir = Path(self.tmp.name) / "in"
        self.public = Path(self.tmp.name) / "public"
        self.input_dir.mkdir()
        (self.input_dir / "summary.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "total_incidents": 12,
                    "date_range": {"first": "2026-01-01", "last": "2026-01-31"},
                    "by_month": {"2026-01": 12},
                    "by_week": {"2026-W01": 12},
                    "hotspots": {},
                    "by_neighborhood": None,
                    "by_category": {"ק": 12},
                    "by_street": {"א": 9, "ב": 1},
                    "by_status": {},
                    "closure": None,
                    "inputs": [],
                    "duplicates_skipped": 0,
                }
            ),
            encoding="utf-8",
        )
        (self.input_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        for name, header in (
            ("monthly.csv", "month,reports"),
            ("categories.csv", "category,reports"),
            ("streets.csv", "street,reports"),
        ):
            (self.input_dir / name).write_text(header + "\n", encoding="utf-8")
        self.mapping = Path(self.tmp.name) / "private-mapping.json"
        self.mapping.write_text(json.dumps({"א": [31.7704, 35.2104]}), encoding="utf-8")

    def _publish(self, **kw: object) -> int:
        from parking_stats.publish import publish

        return publish(self.input_dir, self.public, "label", **kw)  # type: ignore[arg-type]

    def test_publish_writes_heatmap_files(self) -> None:
        self.assertEqual(self._publish(geocoding=self.mapping), 0)
        page = (self.public / "heatmap.html").read_text(encoding="utf-8")
        self.assertIn("א", page)
        payload = json.loads(
            (self.public / "data" / "heatmap.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["cells"][0]["n"], 9)
        index = (self.public / "index.html").read_text(encoding="utf-8")
        self.assertIn("heatmap.html", index)

    def test_without_geocoding_no_heatmap(self) -> None:
        self.assertEqual(self._publish(), 0)
        self.assertFalse((self.public / "heatmap.html").exists())
        index = (self.public / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("heatmap.html", index)

    def test_publish_with_basemap_embeds_map(self) -> None:
        basemap = Path(self.tmp.name) / "basemap.json"
        basemap.write_text(
            '{"lines": [[[31.7704, 35.2104], [31.7710, 35.2110]]]}',
            encoding="utf-8",
        )
        self.assertEqual(self._publish(geocoding=self.mapping, basemap=basemap), 0)
        # #28: the basemap is inlined into the interactive SVG; the
        # standalone basemap.svg is gone and map.js ships same-origin.
        self.assertFalse((self.public / "data" / "basemap.svg").exists())
        self.assertTrue((self.public / "data" / "map.js").exists())
        page = (self.public / "heatmap.html").read_text(encoding="utf-8")
        self.assertNotIn("basemap.svg", page)
        self.assertIn("<path", page)  # inlined street lines
        self.assertIn("https://www.openstreetmap.org/copyright", page)
        self.assertIn("OpenStreetMap contributors</a> (ODbL)", page)

    def test_stale_basemap_svg_removed(self) -> None:
        data = self.public / "data"
        data.mkdir(parents=True)
        (data / "basemap.svg").write_text("<svg/>", encoding="utf-8")
        self.assertEqual(self._publish(geocoding=self.mapping), 0)
        self.assertFalse((self.public / "data" / "basemap.svg").exists())

    def test_stale_heatmap_removed_without_geocoding(self) -> None:
        self.assertEqual(self._publish(geocoding=self.mapping), 0)
        self.assertEqual(self._publish(), 0)
        self.assertFalse((self.public / "heatmap.html").exists())
        self.assertFalse((self.public / "data" / "heatmap.json").exists())

    def test_bad_mapping_refuses_publish(self) -> None:
        self.mapping.write_text('{"א": [99.5, 35.21]}', encoding="utf-8")
        self.assertEqual(self._publish(geocoding=self.mapping), 1)
        self.assertFalse((self.public / "heatmap.html").exists())


if __name__ == "__main__":
    unittest.main()


class BasemapGradient23Test(unittest.TestCase):
    """#23: vendored OSM basemap, heat gradient, labels, legend."""

    MAPPING = BuildCellsTest.MAPPING
    LINES = [
        [(31.7700, 35.2095), (31.7706, 35.2108)],  # crosses the cells
        [(31.60, 35.10), (31.61, 35.11)],  # fully outside the bbox
    ]

    def _cells(self) -> list[heatmap.Cell]:
        cells, _b, _u = heatmap.build_cells({"א": 9, "ג": 4}, self.MAPPING)
        return cells

    def test_basemap_path_clips_to_cells_bbox(self) -> None:
        path = heatmap.build_basemap_path(self.LINES, self._cells())
        self.assertTrue(path.startswith("M"))
        # The far-away line contributes no path commands of its own:
        self.assertEqual(path.count("M"), 1)

    def test_basemap_path_no_lines_in_bbox(self) -> None:
        path = heatmap.build_basemap_path([self.LINES[1]], self._cells())
        self.assertEqual(path, "")

    def test_load_basemap_validation(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bm.json"
            path.write_text('{"lines": [[31.7, 35.2]]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                heatmap.load_basemap(path)
            path.write_text(
                '{"lines": [[[40.0, 35.2], [40.1, 35.3]]]}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                heatmap.load_basemap(path)
            path.write_text(
                '{"lines": [[[31.77, 35.21], [31.78, 35.22]]]}', encoding="utf-8"
            )
            self.assertEqual(len(heatmap.load_basemap(path)), 1)

    def test_gradient_labels_legend_and_attribution(self) -> None:
        page = heatmap.build_heatmap_html(
            self._cells(), 0, 0, "now", basemap_lines=self.LINES
        )
        self.assertIn("radialGradient", page)
        self.assertIn("feGaussianBlur", page)
        self.assertIn("<path", page)  # inlined basemap
        self.assertIn(
            '<a href="https://www.openstreetmap.org/copyright">'
            "OpenStreetMap contributors</a> (ODbL)",
            page,
        )
        # #28: RTL legend - highest on the left, label order "max - min"
        # pinned LTR so bidi does not flip the numbers.
        self.assertIn('דיווחים: <span dir="ltr">9 - 4</span>', page)
        self.assertIn('class="legend"', page)
        self.assertIn(">א</text>", page)  # street label on the cell
        self.assertNotIn("unpkg", page)
        # The single permitted URL is the ODbL attribution anchor.
        self.assertEqual(page.count("https://"), 1)
        self.assertNotIn("http://", page)

    def test_without_basemap_no_path_or_attribution(self) -> None:
        page = heatmap.build_heatmap_html(self._cells(), 0, 0, "now")
        self.assertNotIn("basemap", page)
        self.assertNotIn("OpenStreetMap", page)
        self.assertIn("radialGradient", page)


class InteractiveMap28Test(unittest.TestCase):
    """#28: cropped centred view, zoom/pan script, label collisions."""

    MAPPING = BuildCellsTest.MAPPING

    def test_fallback_number_registered_in_collision_boxes(self) -> None:
        # Five cells at one point: four name slots (above, below, both
        # sides), then a numbered fallback. Every placed piece -
        # including the number - must be registered so later labels
        # never overlap it.
        cells = [
            {"lat": 31.770, "lng": 35.210, "n": 50, "label": "אאאא"},
            {"lat": 31.770, "lng": 35.210, "n": 40, "label": "בבבב"},
            {"lat": 31.770, "lng": 35.210, "n": 30, "label": "גגגג"},
            {"lat": 31.770, "lng": 35.210, "n": 20, "label": "דדדד"},
            {"lat": 31.770, "lng": 35.210, "n": 10, "label": "הההה"},
        ]
        project, _bbox = heatmap._projection(cells)
        _blobs, overlay, placed = heatmap._render_cells(cells, project)
        self.assertEqual(len(placed), 5)
        # Four names and exactly one numbered fallback (row 5)...
        self.assertEqual(sum("הההה" in o for o in overlay), 0)
        import re as _re

        self.assertTrue(any(_re.search(r">5</text>", o) for o in overlay))
        # ...whose collision box is registered.
        gx, gy = project(31.770, 35.210)
        radius5 = heatmap._radius(cells[4], 50)
        self.assertIn(heatmap._fallback_box(gx, gy, radius5, 5), placed)

    def test_numbered_fallback_and_hash_column(self) -> None:
        # Equal counts: identical dots and label boxes, so the third
        # cell's four candidate positions all collide and it falls back
        # to its row number at any label scale.
        cells = [
            {"lat": 31.770, "lng": 35.210, "n": 30, "label": "אאאא"},
            {"lat": 31.770, "lng": 35.210, "n": 30, "label": "בבבב"},
            {"lat": 31.770, "lng": 35.210, "n": 30, "label": "גגגג"},
        ]
        page = heatmap.build_heatmap_html(cells, 0, 0, "now")
        # Row 3 falls back to its number next to the dot...
        self.assertRegex(page, r"<text[^>]*>3</text>")
        # ...and the table's # column carries its name.
        self.assertIn("<th>#</th>", page)
        self.assertIn("<tr><td>3</td><td>גגגג</td><td>30</td></tr>", page)

    def test_initial_view_crops_and_notes_outside_cells(self) -> None:
        # One far-away low cell: the view crops to the dense cluster and
        # the page says how many cells sit outside it (never dropped).
        cells = [
            {"lat": 31.770, "lng": 35.210, "n": 100, "label": "א"},
            {"lat": 31.771, "lng": 35.211, "n": 90, "label": "ב"},
            {"lat": 31.769, "lng": 35.209, "n": 80, "label": "ג"},
            {"lat": 31.850, "lng": 35.350, "n": 4, "label": "רחוקה"},
        ]
        page = heatmap.build_heatmap_html(cells, 0, 0, "now")
        self.assertIn('data-home="', page)
        self.assertIn('data-world="0,0,76,48"', page)
        self.assertIn('data-min-span="6"', page)
        view = page.split('data-home="', 1)[1].split('"', 1)[0].split(",")
        self.assertNotEqual([view[0], view[1]], ["0.00", "0.00"])
        self.assertLess(float(view[2]), 76.0)  # cropped, not the whole world
        self.assertIn("מוקד נוסף אחד מחוץ לתצוגה הראשונית", page)
        # The outside cell is still in the table - no silent data drop.
        self.assertIn("<tr><td>4</td><td>רחוקה</td><td>4</td></tr>", page)

    def test_map_js_is_first_party_and_zoom_capped(self) -> None:
        js = heatmap.MAP_JS
        self.assertNotIn("http", js)
        self.assertIn("minSpan", js)  # zoom cap honours data-min-span
        self.assertIn("wheel", js)
        self.assertIn("pointerdown", js)
        self.assertIn("data-reset", js)

    def test_no_js_static_fallback_still_renders(self) -> None:
        page = heatmap.build_heatmap_html(
            [{"lat": 31.770, "lng": 35.210, "n": 9, "label": "א"}], 0, 0, "now"
        )
        # The static centred view is the SVG's own viewBox; the script is
        # deferred and the controls start hidden for no-JS readers.
        self.assertIn("<noscript>", page)
        self.assertIn('id="map-controls" hidden', page)
        self.assertIn('viewBox="', page)


class ReviewLows30Test(unittest.TestCase):
    """#30: #29 review lows - view-clipped labels, controls CSS, pan."""

    def test_hottest_cell_at_view_top_stays_named(self) -> None:
        # The hottest cell sits at the top edge of the cropped view: its
        # name must move inside the view (below the dot), never clip.
        cells = [
            {"lat": 31.780, "lng": 35.210, "n": 100, "label": "חמה"},
            {"lat": 31.770, "lng": 35.211, "n": 90, "label": "ב"},
            {"lat": 31.769, "lng": 35.209, "n": 80, "label": "ג"},
            {"lat": 31.771, "lng": 35.208, "n": 70, "label": "ד"},
        ]
        page = heatmap.build_heatmap_html(cells, 0, 0, "now")
        vx, vy, vw, vh = (
            float(v)
            for v in page.split('data-home="', 1)[1].split('"', 1)[0].split(",")
        )
        import re as _re

        m = _re.search(r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>חמה</text>', page)
        self.assertIsNotNone(m, "hottest street must keep its name label")
        ly = float(m.group(2))
        self.assertGreaterEqual(ly, vy)
        self.assertLessEqual(ly, vy + vh)

    def test_controls_hidden_without_js_and_no_text_selection(self) -> None:
        page = heatmap.build_heatmap_html(
            [{"lat": 31.770, "lng": 35.210, "n": 9, "label": "א"}], 0, 0, "now"
        )
        self.assertIn(".controls[hidden]", page)  # beats display:flex w/o JS
        self.assertIn("user-select: none", page)  # drag-pan selects nothing


class GradientOnly30Test(unittest.TestCase):
    """#30: gradient-only heat, basemap over heat, top streets named."""

    # The ten hottest cells, copied from the published aggregate
    # public/data/heatmap.json (rounded ~111 m cells, counts, street
    # names - all public on the Pages site).
    REAL_CELLS: list[heatmap.Cell] = [
        {"lat": 31.77, "lng": 35.212, "n": 320, "label": "הרב חיים ברלין / חרלפ"},
        {"lat": 31.766, "lng": 35.21, "n": 246, "label": "הפורצים"},
        {"lat": 31.766, "lng": 35.209, "n": 50, "label": "הפלמח"},
        {"lat": 31.765, "lng": 35.211, "n": 28, "label": "כובשי קטמון"},
        {"lat": 31.763, "lng": 35.212, "n": 19, "label": "בוסתנאי"},
        {"lat": 31.757, "lng": 35.215, "n": 17, "label": "גנרל פייר קניג"},
        {"lat": 31.772, "lng": 35.213, "n": 11, "label": "דרך עזה"},
        {"lat": 31.753, "lng": 35.216, "n": 10, "label": "פועלי צדק"},
        {"lat": 31.763, "lng": 35.214, "n": 7, "label": "חזקיהו המלך"},
        {"lat": 31.792, "lng": 35.208, "n": 7, "label": "ירמיהו"},
        {"lat": 31.761, "lng": 35.215, "n": 6, "label": "אלעזר המודעי"},
        {"lat": 31.765, "lng": 35.21, "n": 6, "label": "הלה"},
        {"lat": 31.79, "lng": 35.175, "n": 6, "label": "הקבלן"},
        {"lat": 31.761, "lng": 35.205, "n": 6, "label": 'שדרות ש"י עגנון'},
        {"lat": 31.738, "lng": 35.18, "n": 5, "label": "אודם"},
        {"lat": 31.769, "lng": 35.212, "n": 5, "label": "איתמר בן אבי"},
        {"lat": 31.771, "lng": 35.211, "n": 5, "label": "בן סרוק"},
        {"lat": 31.785, "lng": 35.215, "n": 5, "label": "יפו"},
    ]
    REAL_TOP10 = REAL_CELLS[:10]
    LINES = [
        [(31.750, 35.205), (31.770, 35.212), (31.795, 35.218)],
        [(31.766, 35.200), (31.766, 35.222)],
    ]

    def test_top10_all_named_in_default_view(self) -> None:
        import re as _re

        page = heatmap.build_heatmap_html(
            list(self.REAL_CELLS), 0, 0, "now", basemap_lines=self.LINES
        )
        vx, vy, vw, vh = (
            float(v)
            for v in page.split('data-home="', 1)[1].split('"', 1)[0].split(",")
        )
        for i, cell in enumerate(self.REAL_CELLS, 1):
            m = _re.search(
                r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>'
                + _re.escape(cell["label"])
                + "</text>",
                page,
            )
            if i <= len(self.REAL_TOP10):
                self.assertIsNotNone(m, f"row {i} {cell['label']} lost its name")
            if m is None:
                continue
            ly = float(m.group(2))
            # A label is either fully inside the view (its dot is in
            # view) or fully outside it (appears when panned to) -
            # never clipped across the edge.
            half = len(cell["label"]) * 0.85 * (vw / 76)
            lx = float(m.group(1))
            box = (lx - half, ly - 2.0 * vw / 76, lx + half, ly + 0.4 * vw / 76)
            inside = (
                vx <= box[0]
                and box[2] <= vx + vw
                and vy <= box[1]
                and box[3] <= vy + vh
            )
            outside = not (
                box[0] < vx + vw and box[2] > vx and box[1] < vy + vh and box[3] > vy
            )
            self.assertTrue(
                inside or outside,
                f"{cell['label']} label clips across the view edge",
            )
            # No numbered fallback for any top-10 row.
            self.assertIsNone(
                _re.search(r"<text [^>]*>%d</text>" % i, page),
                f"row {i} fell back to its number",
            )

    def test_gradient_only_structure(self) -> None:
        page = heatmap.build_heatmap_html(
            list(self.REAL_CELLS), 0, 0, "now", basemap_lines=self.LINES
        )
        svg = page.split('<svg id="map"', 1)[1].split("</svg>", 1)[0]
        # No solid marker dots; only blurred gradient blobs.
        self.assertNotIn('fill-opacity="0.95"', svg)
        self.assertIn("feGaussianBlur", svg)
        # Draw order: heat blobs, then the basemap over them, then labels.
        self.assertLess(svg.index("<circle"), svg.index("<path"))
        self.assertLess(svg.index("<path"), svg.index("<text"))

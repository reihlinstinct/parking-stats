"""Tests for the landing-page SVG charts."""

from __future__ import annotations

import unittest

from parking_stats.charts import hbar_chart, vbar_chart


class ChartTest(unittest.TestCase):
    def test_vbar_renders_all_rows_with_counts(self):
        svg = vbar_chart([("2026-08", 10), ("2026-09", 42), ("2026-10", 27)])
        self.assertIn("2026-09", svg)
        self.assertIn(">42</text>", svg)
        self.assertEqual(svg.count("<rect"), 3)

    def test_hbar_scales_widest_bar_to_max(self):
        svg = hbar_chart([("א", 100), ("ב", 50)])
        self.assertIn("א", svg)
        self.assertEqual(svg.count("<rect"), 2)

    def test_labels_are_escaped(self):
        svg = hbar_chart([("רחוב <script>x</script>", 5)])
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_empty_rows_render_nothing(self):
        self.assertEqual(vbar_chart([]), "")
        self.assertEqual(hbar_chart([]), "")

    def test_svg_number_lists_use_commas(self):
        # Space-separated number chains trip the repo plate-compact rule.
        svg = vbar_chart([("a", 1)]) + hbar_chart([("b", 1)])
        self.assertIn('viewBox="0,0,', svg)
        self.assertNotIn('viewBox="0 0', svg)


if __name__ == "__main__":
    unittest.main()

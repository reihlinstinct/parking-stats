"""Named acceptance test 5: monthly snapshot month (#36)."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from parking_stats.monthly import JERUSALEM, snapshot_month, valid_dispatch_key

UTC = ZoneInfo("UTC")


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class SnapshotMonthTests(unittest.TestCase):
    def test_day_one_derives_the_just_completed_month(self):
        self.assertEqual(snapshot_month(at(2026, 12, 1, 3, 17)), "2026-11")
        self.assertEqual(snapshot_month(at(2027, 1, 1, 0, 5)), "2026-12")

    def test_delayed_start_day_two_before_six_derives_the_same_key(self):
        # 2026-12-02 05:00 Asia/Jerusalem == 03:00 UTC (UTC+2 in December).
        self.assertEqual(snapshot_month(at(2026, 12, 2, 3, 0)), "2026-11")

    def test_day_two_at_six_or_later_exits(self):
        self.assertIsNone(snapshot_month(at(2026, 12, 2, 4, 0)))  # 06:00 local
        self.assertIsNone(snapshot_month(at(2026, 12, 5, 1, 0)))

    def test_naive_start_is_interpreted_as_utc(self):
        naive = datetime(2026, 12, 1, 3, 17)
        self.assertEqual(snapshot_month(naive), "2026-11")
        self.assertEqual(JERUSALEM.key, "Asia/Jerusalem")


class DispatchKeyTests(unittest.TestCase):
    NOW = at(2026, 11, 18, 3, 0)

    def test_valid_keys(self):
        self.assertTrue(valid_dispatch_key("2026-10", self.NOW))
        self.assertTrue(valid_dispatch_key("2026-11", self.NOW))
        self.assertTrue(valid_dispatch_key("2020-01", self.NOW))

    def test_future_month_rejected(self):
        self.assertFalse(valid_dispatch_key("2026-12", self.NOW))
        self.assertFalse(valid_dispatch_key("2027-01", self.NOW))

    def test_malformed_keys_rejected_before_any_work(self):
        for key in (
            "2026-13",
            "2026-00",
            "202611",
            "2026-1",
            "2026/11",
            "abcd-ef",
            "",
            "2026-11-01",
            " 2026-11",
        ):
            self.assertFalse(valid_dispatch_key(key, self.NOW))


if __name__ == "__main__":
    unittest.main()

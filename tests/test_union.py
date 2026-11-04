import copy
import unittest
from unittest.mock import patch

from parking_stats.union import UnionError, aggregate, reduce_events, validate_aggregate

REF = "2610-" + "9" * 6 + "1"
REF2 = "2610-" + "9" * 6 + "2"


def event(ref=REF, source="mail-1", origin="city_email", **fields):
    return dict(
        **dict(case_number=ref),
        source_id=source,
        origin=origin,
        observed_at="2026-11-04T10:00:00+02:00",
        fields=fields,
    )


class UnionTests(unittest.TestCase):
    def test_overlap_replay_and_order(self):
        a = event(createdon="2026-10-08T12:00", streetname="alpha")
        b = event(source="rev1/row1", origin="ledger", createdon="2026-10-08T12:00:50")
        x = reduce_events([a, b, a])
        self.assertEqual(x, reduce_events([b, a]))
        self.assertEqual(len(x["incidents"]), 1)
        self.assertEqual(len(x["incidents"][0]["sources"]), 2)
        self.assertEqual(a["fields"]["streetname"], "alpha")

    def test_suffix_distinct_and_guid_rejected(self):
        self.assertEqual(
            len(reduce_events([event(), event(REF + "/1", "s2")])["incidents"]), 2
        )
        with self.assertRaises(UnionError):
            reduce_events([event("23a8a01d-1dc5-f111-aaad-6045bd89af0f")])

    def test_source_reassignment_fails(self):
        with self.assertRaisesRegex(UnionError, "source_replay_conflict"):
            reduce_events([event(), event(REF2)])

    def test_unknown_and_conflicting_fields(self):
        payload = aggregate(
            [
                event(streetname="private address 12", subjectname="raw secret"),
                event(source="s2", origin="ledger", createdon="2026-10-01T12:00"),
                event(source="s3", createdon="2026-10-02T12:00"),
            ],
            streets={"alpha"},
            categories={"parking"},
            unresolved_rows=7,
        )
        self.assertEqual(payload["case_identifiers"], 1)
        self.assertEqual(payload["by_street"], {"other_or_unknown": 1})
        self.assertEqual(payload["missing_opening_date"], 1)
        self.assertNotIn("private", str(payload))
        self.assertEqual(payload["unresolved_rows"], 7)

    def test_same_day_precision_not_conflict(self):
        p = aggregate(
            [
                event(createdon="2026-10-08T10:10"),
                event(source="s2", createdon="2026-10-08T10:10:45"),
            ],
            streets=set(),
            categories=set(),
        )
        self.assertEqual(p["by_month"], {"2026-10": 1})

    def test_hostile_contracts(self):
        p = aggregate([event()], streets=set(), categories=set())
        for key, value in [
            ("case_number", REF),
            ("case_identifiers", True),
            ("by_street", {"raw label": 1}),
            ("by_month", {"2026-13": 1}),
        ]:
            bad = copy.deepcopy(p)
            bad[key] = value
            with self.assertRaises(UnionError):
                validate_aggregate(bad, streets=set(), categories=set())
        with self.assertRaises(UnionError):
            aggregate([event()], streets={"alpha 12"}, categories=set())

    def test_raw_unexpected_fields_rejected(self):
        with self.assertRaises(UnionError):
            reduce_events([event(**dict(description="raw private text"))])
        bad = event()
        bad["approval"] = "publish everything"
        with self.assertRaises(UnionError):
            reduce_events([bad])

    def test_invalid_origin_types_are_safe(self):
        for value in ([], {}, None, True):
            bad = event()
            bad["origin"] = value
            with self.assertRaises(UnionError):
                reduce_events([bad])

    def test_empty_data(self):
        p = aggregate([], streets=set(), categories=set())
        self.assertEqual(p["case_identifiers"], 0)
        self.assertIsNone(p["through"])

    def test_distinct_numbers_same_scene_stay_separate(self):
        p = aggregate(
            [
                event(streetname="alpha"),
                event(REF2, "mail-2", streetname="alpha"),
            ],
            streets={"alpha"},
            categories=set(),
        )
        self.assertEqual(p["case_identifiers"], 2)
        self.assertEqual(p["by_street"], {"alpha": 2})

    def test_wrong_payload_shapes_safe(self):
        for value in (None, [], "text", 1, True, set()):
            with self.assertRaises(UnionError):
                validate_aggregate(value, streets=set(), categories=set())

    def test_wrong_label_shapes_safe(self):
        for value in (None, [], {}, "text", 1, True):
            for key in ("streets", "categories"):
                kwargs = dict(streets=set(), categories=set())
                kwargs[key] = value
                with self.assertRaises(UnionError):
                    aggregate([], **kwargs)

    def test_input_and_case_cap_boundaries(self):
        with patch("parking_stats.union.MAX_CASES", 1):
            self.assertEqual(len(reduce_events([event()] * 10)["incidents"]), 1)
            with self.assertRaisesRegex(UnionError, "input_limit"):
                reduce_events([event()] * 11)
            with self.assertRaisesRegex(UnionError, "case_limit"):
                reduce_events([event(), event(REF2, "s2")])

    def test_sum_consistency_each_group(self):
        good = aggregate(
            [event(createdon="2026-10-08T12:00")], streets=set(), categories=set()
        )
        for key in ("by_street", "by_category", "by_month"):
            bad = copy.deepcopy(good)
            group = next(iter(bad[key]))
            bad[key][group] = 2
            with self.assertRaisesRegex(UnionError, "public_sum"):
                validate_aggregate(bad, streets=set(), categories=set())

    def test_invalid_scalar_counts(self):
        p = aggregate([event()], streets=set(), categories=set())
        for key in ("case_identifiers", "unresolved_rows", "missing_opening_date"):
            for value in (-1, 100001, True, 1.0, "1", None, []):
                bad = copy.deepcopy(p)
                bad[key] = value
                with self.assertRaises(UnionError):
                    validate_aggregate(bad, streets=set(), categories=set())

    def test_invalid_group_counts(self):
        p = aggregate(
            [event(createdon="2026-10-08T12:00")], streets=set(), categories=set()
        )
        for key in ("by_street", "by_category", "by_month"):
            for value in (0, -1, 100001, True, 1.0, "1", None, []):
                bad = copy.deepcopy(p)
                bad[key][next(iter(bad[key]))] = value
                with self.assertRaises(UnionError):
                    validate_aggregate(bad, streets=set(), categories=set())

    def test_date_coverage_consistency(self):
        p = aggregate(
            [event(createdon="2026-10-08T12:00")], streets=set(), categories=set()
        )
        for changes in (
            dict(from_date=None),
            dict(through=None),
            dict(from_date="2026-11-01"),
            dict(through="2026-09-01"),
            dict(from_date="2026-09-01"),
            dict(through="2026-11-01"),
            dict(missing_opening_date=2),
        ):
            bad = copy.deepcopy(p)
            bad.update(changes)
            with self.assertRaises(UnionError):
                validate_aggregate(bad, streets=set(), categories=set())

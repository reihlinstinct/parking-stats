import copy
import unittest

from parking_stats.union import UnionError, aggregate, reduce_events, validate_aggregate


def event(ref="2610-9000001", source="mail-1", origin="city_email", **fields):
    return dict(
        case_number=ref,
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
            len(reduce_events([event(), event("2610-9000001/1", "s2")])["incidents"]), 2
        )
        with self.assertRaises(UnionError):
            reduce_events([event("23a8a01d-1dc5-f111-aaad-6045bd89af0f")])

    def test_source_reassignment_fails(self):
        with self.assertRaisesRegex(UnionError, "source_replay_conflict"):
            reduce_events([event(), event("2610-9000002")])

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
            ("case_number", "2610-9000001"),
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
            reduce_events([event(description="raw private text")])
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
                event("2610-9000002", "mail-2", streetname="alpha"),
            ],
            streets={"alpha"},
            categories=set(),
        )
        self.assertEqual(p["case_identifiers"], 2)
        self.assertEqual(p["by_street"], {"alpha": 2})

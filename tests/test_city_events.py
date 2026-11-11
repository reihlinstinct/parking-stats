"""Canonical city-event input, FIFO/transport-ID ledger rows and CSV tolerance."""

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from parking_stats.private_builder import build
from parking_stats.union import UnionError

SPEC = importlib.util.spec_from_file_location(
    "private_fetch", Path(__file__).parents[1] / "scripts/fetch_parking_source.py"
)
assert SPEC is not None and SPEC.loader is not None
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)

BASE = "2610-" + "9" * 6 + "1"
CITY = "2611-" + "8" * 6 + "1"
OTHER = "2611-" + "8" * 6 + "2"
STAMP = "2026-11-04T10:00:00+02:00"
CITY_STAMP = "2026-11-11T12:16:06+02:00"
UUID = "123e4567-e89b-12d3-a456-426614174000"


def city_event(case, source="gmail/abc1", **fields):
    return dict(
        origin="city_email",
        case_number=case,
        source_id=source,
        observed_at=CITY_STAMP,
        fields=fields or dict(createdon="2026-11-10", streetname="alpha"),
    )


class CityEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.private, self.plain = root / "private", root / "plain"
        (self.private / "ledger").mkdir(parents=True)
        self.plain.mkdir()
        self.baseline = dict(
            snapshot_at=STAMP,
            incidents=[
                dict(
                    case_number=BASE,
                    origin="email_export",
                    createdon="2026-10-08T10:00",
                    streetname="alpha",
                    subjectname="parking",
                )
            ],
            conflicts=[],
            unresolved_filed_rows=[],
        )
        self.rows = []
        self.add_row("row1", municipal_ref=BASE, ref_mapping="verified")
        self.events = None
        self.write()

    def add_row(self, folder, **values):
        row = dict(
            folder_id=folder,
            filed_date="2026-11-08",
            municipal_ref="",
            street="alpha",
            city_opened_at="",
            ref_mapping="verified",
            metadata_record="",
            municipal_event_number="",
        )
        row.update(values)
        self.rows.append(row)

    def write(self, raw_csv=None):
        (self.plain / "reports_raw_recovered_union.json").write_text(
            json.dumps(self.baseline)
        )
        path = self.private / "ledger/reports.csv"
        if raw_csv is not None:
            path.write_text(raw_csv)
        else:
            with path.open("w") as f:
                writer = csv.DictWriter(f, fieldnames=list(self.rows[0]))
                writer.writeheader()
                writer.writerows(self.rows)
        events = self.private / "ledger/stats-city-events.json"
        if self.events is not None:
            events.write_text(json.dumps(self.events))

    def run_build(self):
        return build(
            self.private,
            self.plain,
            streets={"alpha"},
            categories={"parking"},
            ledger_observed_at=STAMP,
        )

    def envelope(self, *events, **overrides):
        value = dict(schema_version=1, snapshot_at=CITY_STAMP, events=list(events))
        value.update(overrides)
        return value

    def test_city_events_add_cases_and_dedupe_by_exact_number(self):
        self.events = self.envelope(
            city_event(CITY),
            city_event(OTHER, "gmail/abc2", createdon="2026-11-11"),
            city_event(BASE, "gmail/abc3", createdon="2026-10-08"),
        )
        self.write()
        payload, fresh = self.run_build()
        self.assertEqual(payload["case_identifiers"], 3)
        self.assertEqual(payload["through"], "2026-11-11")
        self.assertEqual(fresh["city_events_snapshot_at"], CITY_STAMP)
        self.assertIs(fresh["live_city_status"], False)
        self.assertEqual(fresh["schema_version"], 2)
        dumped = json.dumps([payload, fresh])
        for private in (BASE, CITY, OTHER, "gmail/abc1"):
            self.assertNotIn(private, dumped)

    def test_conflicting_city_date_becomes_unknown_not_overwritten(self):
        self.events = self.envelope(city_event(BASE, createdon="2026-10-09"))
        self.write()
        payload, _ = self.run_build()
        self.assertEqual(payload["case_identifiers"], 1)
        self.assertEqual(payload["missing_opening_date"], 1)

    def test_absent_file_means_no_city_snapshot(self):
        _, fresh = self.run_build()
        self.assertIsNone(fresh["city_events_snapshot_at"])

    def test_envelope_and_event_validation_fails_closed(self):
        bad = [
            self.envelope(extra=1),
            dict(schema_version=1, snapshot_at=CITY_STAMP),
            self.envelope(schema_version=2),
            self.envelope(snapshot_at="2026-11-11T12:00:00"),
            self.envelope(events="x"),
            self.envelope(dict(city_event(CITY), origin="ledger")),
            self.envelope(city_event(CITY, plate="x")),
            self.envelope(dict(city_event(CITY), fields=dict(plate="12-345-67"))),
            self.envelope(dict(city_event(CITY), observed_at="2026-11-10")),
            self.envelope(dict(city_event(CITY), case_number="not-a-case")),
            self.envelope(dict(city_event(CITY), body="free text")),
        ]
        for events in bad:
            with self.subTest(events=events):
                self.events = events
                self.write()
                with self.assertRaises(UnionError):
                    self.run_build()

    def test_same_source_replay_conflict_fails(self):
        self.events = self.envelope(
            city_event(CITY), city_event(CITY, createdon="2026-11-09")
        )
        self.write()
        with self.assertRaisesRegex(UnionError, "source_replay_conflict"):
            self.run_build()

    def test_unverified_fifo_row_is_unresolved_even_with_conflicting_refs(self):
        self.add_row(
            "fifo",
            ref_mapping="unverified_fifo",
            municipal_ref=CITY,
            municipal_event_number=OTHER,
        )
        self.write()
        payload, _ = self.run_build()
        self.assertEqual(payload["case_identifiers"], 1)
        self.assertEqual(payload["unresolved_rows"], 1)
        # The independent city events own those case numbers.
        self.events = self.envelope(city_event(CITY), city_event(OTHER, "gmail/abc2"))
        self.write()
        payload, _ = self.run_build()
        self.assertEqual(payload["case_identifiers"], 3)
        self.assertEqual(payload["unresolved_rows"], 1)

    def test_uuid_event_number_is_a_transport_id(self):
        self.add_row("uuid", municipal_event_number=UUID)
        self.add_row("uuid2", municipal_ref=OTHER, municipal_event_number=UUID)
        self.write()
        payload, _ = self.run_build()
        self.assertEqual(payload["unresolved_rows"], 1)
        self.assertEqual(payload["case_identifiers"], 2)

    def test_verified_conflicting_refs_still_rejected(self):
        self.add_row("bad", municipal_ref=CITY, municipal_event_number=OTHER)
        self.write()
        with self.assertRaisesRegex(UnionError, "ledger_reference_conflict"):
            self.run_build()

    def test_empty_surplus_cells_ok_but_nonempty_overflow_rejected(self):
        header = (
            "folder_id,filed_date,municipal_ref,street,city_opened_at,"
            "ref_mapping,metadata_record,municipal_event_number"
        )
        row = f"row1,2026-11-08,{BASE},alpha,,verified,,"
        self.write(raw_csv=f"{header}\n{row},,,,,\n")
        self.assertEqual(self.run_build()[0]["case_identifiers"], 1)
        self.write(raw_csv=f"{header}\n{row},,,x,\n")
        with self.assertRaisesRegex(UnionError, "ledger_overflow"):
            self.run_build()


class FetchSurplusCellTests(unittest.TestCase):
    HEAD = "folder_id,metadata_record,ref_mapping\n"

    def test_empty_trailing_cells_are_accepted(self):
        raw = (self.HEAD + "row,reports/row/report.json,verified,,,,,\n").encode()
        self.assertEqual(FETCH.required_paths(raw), ["reports/row/report.json"])

    def test_nonempty_overflow_and_short_rows_are_rejected(self):
        for body in (
            "row,reports/row/report.json,verified,,x\n",
            "row,reports/row/report.json,verified,../../etc/passwd\n",
            "row,reports/row/report.json\n",
        ):
            with self.subTest(body=body):
                with self.assertRaises(FETCH.FetchError) as error:
                    FETCH.required_paths((self.HEAD + body).encode())
                self.assertEqual(error.exception.code, "source_path")


if __name__ == "__main__":
    unittest.main()

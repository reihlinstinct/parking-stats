import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parking_stats.private_builder import Reader, build
from parking_stats.union import UnionError

REF = "2610-" + "9" * 6 + "1"
REF2 = "2610-" + "9" * 6 + "2"
STAMP = "2026-11-04T10:00:00+02:00"


class PrivateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.private = self.root / "private"
        self.plain = self.root / "plain"
        self.private.mkdir()
        self.plain.mkdir()
        (self.private / "ledger").mkdir()
        self.baseline = dict(
            snapshot_at=STAMP,
            incidents=[
                dict(
                    case_number=REF,
                    origin="email_export",
                    createdon="2026-10-08T10:00",
                    streetname="alpha",
                    subjectname="parking",
                )
            ],
            conflicts=[],
            unresolved_filed_rows=[],
        )
        self.rows = [
            dict(
                folder_id="row1",
                filed_date="2026-10-08",
                municipal_ref=REF,
                street="alpha",
                city_opened_at="",
                ref_mapping="verified",
                metadata_record="",
                municipal_event_number="",
            )
        ]
        self.write()

    def write(self):
        (self.plain / "reports_raw_recovered_union.json").write_text(
            json.dumps(self.baseline)
        )
        with (self.private / "ledger/reports.csv").open("w") as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0]))
            w.writeheader()
            w.writerows(self.rows)

    def run_build(self):
        return build(
            self.private,
            self.plain,
            streets={"alpha"},
            categories={"parking"},
            ledger_observed_at=STAMP,
        )

    def test_overlap_and_stale_email(self):
        p, f = self.run_build()
        self.assertEqual(p["case_identifiers"], 1)
        self.assertTrue(f["email_stale"])
        self.assertEqual(f["email_snapshot_at"], STAMP)
        self.assertNotIn(REF, json.dumps([p, f]))
        self.assertEqual(self.run_build(), (p, f))

    def test_new_unknown_category_and_unresolved(self):
        new = copy.deepcopy(self.rows[0])
        new.update(folder_id="row2", municipal_ref=REF2)
        pending = copy.deepcopy(new)
        pending.update(folder_id="row3", municipal_ref="")
        self.rows.extend([new, pending])
        self.write()
        p, _ = self.run_build()
        self.assertEqual(p["case_identifiers"], 2)
        self.assertEqual(p["unresolved_rows"], 1)
        self.assertEqual(p["by_category"]["other_or_unknown"], 1)

    def test_duplicate_baseline_and_ledger_rejected(self):
        self.baseline["incidents"] *= 2
        self.write()
        with self.assertRaises(UnionError):
            self.run_build()
        self.baseline["incidents"] = self.baseline["incidents"][:1]
        self.rows *= 2
        self.write()
        with self.assertRaises(UnionError):
            self.run_build()

    def test_symlink_and_traversal_rejected(self):
        (self.private / "linked").symlink_to(self.plain, target_is_directory=True)
        for name in (
            "../plain/reports_raw_recovered_union.json",
            "linked/reports_raw_recovered_union.json",
        ):
            with self.assertRaises(UnionError):
                Reader(self.private).text(name)

    def test_size_limit_and_invalid_json(self):
        with patch("parking_stats.private_builder.MAX_FILE_BYTES", 1):
            with self.assertRaises(UnionError):
                self.run_build()
        (self.plain / "reports_raw_recovered_union.json").write_text(
            '{"incidents":[],"incidents":[]}'
        )
        with self.assertRaises(UnionError):
            self.run_build()

    def test_reference_conflict_and_record_path(self):
        self.rows[0]["municipal_event_number"] = REF2
        self.write()
        with self.assertRaises(UnionError):
            self.run_build()
        self.rows[0]["municipal_event_number"] = ""
        self.rows[0]["metadata_record"] = "../secret"
        self.write()
        with self.assertRaises(UnionError):
            self.run_build()

    def test_missing_referenced_record_fails_closed(self):
        self.rows[0]["metadata_record"] = "reports/row1/report.json"
        self.write()
        with self.assertRaises(UnionError):
            self.run_build()

    def test_conflicts_remain_unknown(self):
        self.baseline["conflicts"] = [
            dict(
                case_number=REF,
                field="streetname",
                email_value="beta",
                message_id="msg1",
            )
        ]
        self.write()
        p, _ = self.run_build()
        self.assertEqual(p["by_street"], {"other_or_unknown": 1})

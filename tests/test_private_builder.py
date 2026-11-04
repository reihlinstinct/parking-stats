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

    def test_duplicate_missing_and_empty_headers(self):
        path = self.private / "ledger/reports.csv"
        for text in (
            "folder_id,municipal_ref,municipal_ref\nrow1,x,\n",
            "folder_id\nrow1\n",
            ",folder_id\nx,row1\n",
            "",
        ):
            path.write_text(text)
            with self.assertRaises(UnionError):
                self.run_build()
        self.write()
        path.write_text(path.read_text().splitlines()[0] + "\n")
        with self.assertRaisesRegex(UnionError, "ledger_empty"):
            self.run_build()

    def test_short_row_rejected(self):
        path = self.private / "ledger/reports.csv"
        text = path.read_text().splitlines()[0] + "\nrow1\n"
        path.write_text(text)
        with self.assertRaisesRegex(UnionError, "ledger_short_row"):
            self.run_build()

    def test_shared_byte_budget(self):
        baseline_size = (self.plain / "reports_raw_recovered_union.json").stat().st_size
        ledger_size = (self.private / "ledger/reports.csv").stat().st_size
        with patch(
            "parking_stats.private_builder.MAX_TOTAL_BYTES", baseline_size + ledger_size
        ):
            self.run_build()
        with patch(
            "parking_stats.private_builder.MAX_TOTAL_BYTES",
            baseline_size + ledger_size - 1,
        ):
            with self.assertRaisesRegex(UnionError, "input_total_size"):
                self.run_build()

    def test_wrong_conflict_key_types_safe(self):
        for value in ([], {}, None, True):
            self.baseline["conflicts"] = [
                dict(
                    case_number=value,
                    field="streetname",
                    email_value="beta",
                    message_id="msg1",
                )
            ]
            self.write()
            with self.assertRaises(UnionError):
                self.run_build()
            self.baseline["conflicts"] = [
                dict(
                    case_number=REF, field=value, email_value="beta", message_id="msg1"
                )
            ]
            self.write()
            with self.assertRaises(UnionError):
                self.run_build()

    def test_symlink_root_ancestor_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(UnionError, "input_root"):
            Reader(alias / "private")

    def test_streamed_row_cap_boundaries(self):
        with patch("parking_stats.private_builder.MAX_CASES", 1):
            self.run_build()
            other = copy.deepcopy(self.rows[0])
            other["folder_id"] = "row2"
            self.rows.append(other)
            self.write()
            with self.assertRaisesRegex(UnionError, "ledger_limit"):
                self.run_build()

    def test_fail_before_read_for_unsafe_and_large(self):
        target = self.private / "target"
        target.write_text("data")
        linked = self.private / "link"
        linked.symlink_to(target)
        for path, limit in (("link", 100), ("../plain/file", 100), ("target", 3)):
            with (
                patch("parking_stats.private_builder.MAX_FILE_BYTES", limit),
                patch.object(
                    Path, "read_text", side_effect=AssertionError("read too early")
                ) as read_text,
                patch.object(
                    Path, "read_bytes", side_effect=AssertionError("read too early")
                ) as read_bytes,
            ):
                with self.assertRaises(UnionError):
                    Reader(self.private).text(path)
                read_text.assert_not_called()
                read_bytes.assert_not_called()

    def test_total_budget_fails_before_read_on_second_root(self):
        from parking_stats.private_builder import Budget

        first = self.private / "first"
        second = self.plain / "second"
        first.write_text("abc")
        second.write_text("def")
        budget = Budget()
        a, b = Reader(self.private, budget), Reader(self.plain, budget)
        with patch("parking_stats.private_builder.MAX_TOTAL_BYTES", 5):
            self.assertEqual(a.text("first"), "abc")
            with patch.object(
                Path, "read_text", side_effect=AssertionError("read too early")
            ) as spy:
                with self.assertRaisesRegex(UnionError, "input_total_size"):
                    b.text("second")
                spy.assert_not_called()

    def test_csv_error_sanitized(self):
        with patch(
            "parking_stats.private_builder.csv.DictReader",
            side_effect=__import__("csv").Error("private input"),
        ):
            with self.assertRaises(UnionError):
                self.run_build()

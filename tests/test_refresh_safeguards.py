"""Negative tests: each safeguard of the refresh fails when it is removed.

Every case builds a hostile or malformed synthetic input and asserts the
specific fail-closed behavior, so deleting the guard turns the test red.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parking_stats.private_builder import build
from parking_stats.source_refresh import (
    PAGE,
    PUBLISHED,
    artifact_gate,
    diff_gate,
    freshness,
)
from parking_stats.union import UnionError

STAMP = "2026-11-04T10:00:00+02:00"
NAIVE = "2026-11-04T10:00:00"
CASE = "2611-" + "7" * 6 + "1"
META = dict(
    schema_version=2,
    refresh_mode="private_checkout_pull",
    ledger_observed_at=STAMP,
    email_snapshot_at=STAMP,
    email_refresh_mode="historical_snapshot_not_live",
    email_stale=True,
    city_events_snapshot_at=STAMP,
    live_city_status=False,
)


def event(**overrides):
    value = dict(
        origin="city_email",
        case_number=CASE,
        source_id="gmail/abc",
        observed_at=STAMP,
        fields=dict(createdon="2026-11-10"),
    )
    value.update(overrides)
    return value


class FreshnessSafeguards(unittest.TestCase):
    def test_live_city_status_must_be_false(self):
        for bad in (True, None, "false", 0):
            with self.subTest(value=bad), self.assertRaises(UnionError):
                freshness(dict(META, live_city_status=bad))

    def test_every_timestamp_must_be_timezone_aware(self):
        for key in (
            "ledger_observed_at",
            "email_snapshot_at",
            "city_events_snapshot_at",
        ):
            for bad in (NAIVE, "2026-11-04", "raw", 5):
                with self.subTest(key=key, value=bad), self.assertRaises(UnionError):
                    freshness(dict(META, **{key: bad}))

    def test_only_the_city_snapshot_may_be_null(self):
        freshness(dict(META, city_events_snapshot_at=None))
        for key in ("ledger_observed_at", "email_snapshot_at"):
            with self.assertRaises(UnionError):
                freshness(dict(META, **{key: None}))

    def test_version_and_modes_are_fixed(self):
        for change in (
            dict(schema_version=1),
            dict(schema_version=True),
            dict(refresh_mode="other"),
            dict(email_refresh_mode="live"),
        ):
            with self.subTest(change=change), self.assertRaises(UnionError):
                freshness(dict(META, **change))


class CityEventBuilderSafeguards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.private, self.plain = root / "private", root / "plain"
        (self.private / "ledger").mkdir(parents=True)
        self.plain.mkdir()
        (self.plain / "reports_raw_recovered_union.json").write_text(
            json.dumps(
                dict(
                    snapshot_at=STAMP,
                    incidents=[],
                    conflicts=[],
                    unresolved_filed_rows=[],
                )
            )
        )
        (self.private / "ledger/reports.csv").write_text(
            "folder_id,filed_date,municipal_ref,street,city_opened_at,"
            "ref_mapping,metadata_record,municipal_event_number\n"
            "row1,2026-11-08,,alpha,,verified,,\n"
        )
        self.events_path = self.private / "ledger/stats-city-events.json"

    def run_build(self):
        return build(
            self.private,
            self.plain,
            streets={"alpha"},
            categories={"parking"},
            ledger_observed_at=STAMP,
        )

    def write(self, *events):
        self.events_path.write_text(
            json.dumps(dict(schema_version=1, snapshot_at=STAMP, events=list(events)))
        )

    def test_good_input_is_accepted(self):
        self.write(event())
        self.assertEqual(self.run_build()[0]["case_identifiers"], 1)

    def test_closed_date_field_is_not_allowed_from_city_events(self):
        # ClosedDateIncident is a reducer field but never a city-event field:
        # no status or closure statistic may enter through this file.
        self.write(
            event(fields=dict(createdon="2026-11-10", ClosedDateIncident="2026-11-11"))
        )
        with self.assertRaisesRegex(UnionError, "city_events_event"):
            self.run_build()

    def test_extra_event_keys_and_fields_are_rejected(self):
        self.write(event(body="free text"))
        with self.assertRaises(UnionError):
            self.run_build()
        self.write(event(fields=dict(createdon="2026-11-10", plate="12-345-67")))
        with self.assertRaises(UnionError):
            self.run_build()

    def test_naive_event_and_snapshot_timestamps_are_rejected(self):
        self.write(event(observed_at=NAIVE))
        with self.assertRaises(UnionError):
            self.run_build()
        self.events_path.write_text(
            json.dumps(dict(schema_version=1, snapshot_at=NAIVE, events=[event()]))
        )
        with self.assertRaises(UnionError):
            self.run_build()

    def test_symlinked_city_events_file_is_rejected(self):
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(
            json.dumps(dict(schema_version=1, snapshot_at=STAMP, events=[event()]))
        )
        self.events_path.symlink_to(target)
        with self.assertRaisesRegex(UnionError, "input_symlink"):
            self.run_build()

    def test_dangling_symlink_is_rejected_not_treated_as_absent(self):
        self.events_path.symlink_to(Path(self.tmp.name) / "missing.json")
        with self.assertRaises(UnionError):
            self.run_build()

    def test_oversize_city_events_file_is_rejected(self):
        self.events_path.write_text(" " * (20 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(UnionError, "input_size_or_type"):
            self.run_build()


def summary(streets):
    counts = {name: 1 for name in streets}
    return dict(
        schema_version=1,
        basis="recovered_case_identifiers_not_certified",
        case_identifiers=len(counts),
        unresolved_rows=0,
        missing_opening_date=len(counts),
        from_date=None,
        through=None,
        by_month={},
        by_street=counts,
        by_category={"other_or_unknown": len(counts)},
    )


class ArtifactGateSafeguards(unittest.TestCase):
    def tree(self, root, streets, payload=None, meta=META):
        (root / "config").mkdir()
        (root / "public/data").mkdir(parents=True)
        (root / "config/parking-source.json").write_text(
            json.dumps(dict(streets=streets, categories=[]))
        )
        (root / "public/data/recovered-summary.json").write_text(
            json.dumps(payload or summary(streets))
        )
        (root / "public/data/source-freshness.json").write_text(json.dumps(meta))

    def test_valid_artifacts_pass(self):
        with tempfile.TemporaryDirectory() as t:
            self.tree(Path(t), ["alpha"])
            artifact_gate(Path(t))

    def test_size_cap_rejects_oversize_artifact(self):
        # 5000 reviewed labels of 100 letters each: schema-valid, over 512 KiB.
        streets = [
            (f"{i:04d}".translate(str.maketrans("0123456789", "אבגדהוזחטי"))).ljust(
                100, "א"
            )
            for i in range(5000)
        ]
        with tempfile.TemporaryDirectory() as t:
            self.tree(Path(t), streets)
            size = (Path(t) / "public/data/recovered-summary.json").stat().st_size
            self.assertGreater(size, 512 * 1024)
            with self.assertRaisesRegex(UnionError, "artifact_size"):
                artifact_gate(Path(t))

    def test_privacy_scan_finding_rejects_artifact(self):
        with tempfile.TemporaryDirectory() as t:
            self.tree(Path(t), ["alpha"])
            with patch(
                "parking_stats.source_refresh.scan_lines", return_value=[object()]
            ):
                with self.assertRaisesRegex(UnionError, "artifact_privacy"):
                    artifact_gate(Path(t))

    def test_unreviewed_label_extra_key_and_bad_freshness_are_rejected(self):
        cases = (
            dict(payload=summary(["beta"])),
            dict(payload=dict(summary(["alpha"]), raw_notes="x")),
            dict(meta=dict(META, live_city_status=True)),
            dict(meta=dict(META, city_events_snapshot_at=NAIVE)),
        )
        for case in cases:
            with self.subTest(case=list(case)), tempfile.TemporaryDirectory() as t:
                self.tree(Path(t), ["alpha"], **case)
                with self.assertRaises(UnionError):
                    artifact_gate(Path(t))


class DiffGateSafeguards(unittest.TestCase):
    def repo(self, root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "--allow-empty",
                "-qm",
                "t",
            ],
            check=True,
        )

    def test_page_is_allowed_only_in_the_published_set(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.repo(root)
            (root / "public").mkdir()
            (root / PAGE).write_text("<p>ok</p>")
            diff_gate(root, PUBLISHED)
            with self.assertRaisesRegex(UnionError, "unexpected_public_diff"):
                diff_gate(root)

    def test_anything_else_is_rejected_even_with_the_published_set(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.repo(root)
            (root / "public").mkdir()
            (root / "public/other.html").write_text("x")
            with self.assertRaisesRegex(UnionError, "unexpected_public_diff"):
                diff_gate(root, PUBLISHED)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parking_stats.source_refresh import diff_gate, freshness, refresh, semantic
from parking_stats.union import UnionError

STAMP = "2026-11-04T10:00:00+02:00"
META = dict(
    schema_version=1,
    refresh_mode="private_checkout_pull",
    ledger_observed_at=STAMP,
    email_snapshot_at=STAMP,
    email_refresh_mode="historical_snapshot_not_live",
    email_stale=True,
)


class SourceRefreshTests(unittest.TestCase):
    def test_freshness_strict_and_clock_noop(self):
        freshness(META)
        later = dict(META, ledger_observed_at="2026-11-04T11:00:00+02:00")
        self.assertEqual(semantic(META), semantic(later))
        for bad in (
            None,
            {},
            dict(META, email_stale=False),
            dict(META, email_snapshot_at="raw"),
            dict(META, private_notes="bad"),
        ):
            with self.assertRaises(UnionError):
                freshness(bad)

    def test_diff_gate_rejects_unexpected_files(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            subprocess.run(["git", "init", "-q", t], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    t,
                    "-c",
                    "user.name=test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "test",
                ],
                check=True,
            )
            (r / "raw.txt").write_text("private")
            with self.assertRaisesRegex(UnionError, "unexpected_public_diff"):
                diff_gate(r)

    def test_hash_failure_has_zero_output_writes(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            public = r / "public"
            private = r / "private"
            tmp = r / "tmp"
            for p in (public, private, tmp):
                p.mkdir()
            cipher = b"-----BEGIN AGE ENCRYPTED FILE-----\ninvalid\n"
            (private / "baseline.age").write_bytes(cipher)
            spec = dict(
                cipher_path="baseline.age",
                cipher_sha256="0" * 64,
                plaintext_sha256="0" * 64,
                streets=[],
                categories=[],
            )
            config = r / "config.json"
            config.write_text(json.dumps(spec))
            with patch(
                "parking_stats.source_refresh.subprocess.run",
                side_effect=AssertionError("must not decrypt"),
            ) as run:
                with self.assertRaisesRegex(UnionError, "cipher_hash"):
                    refresh(public, private, config, r / "identity", STAMP, tmp)
                run.assert_not_called()
            self.assertEqual(list(public.iterdir()), [])

    def test_decrypt_failure_cleans_plaintext_and_leaves_output(self):
        import hashlib

        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            public = r / "public"
            private = r / "private"
            tmp = r / "tmp"
            for p in (public, private, tmp):
                p.mkdir()
            cipher = b"age-encryption.org/v1\nsynthetic"
            (private / "baseline.age").write_bytes(cipher)
            spec = dict(
                cipher_path="baseline.age",
                cipher_sha256=hashlib.sha256(cipher).hexdigest(),
                plaintext_sha256="0" * 64,
                streets=[],
                categories=[],
            )
            config = r / "config.json"
            config.write_text(json.dumps(spec))
            with (
                patch("parking_stats.source_refresh.diff_gate"),
                patch(
                    "parking_stats.source_refresh.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        [], 1, b"", b"private diagnostic"
                    ),
                ),
            ):
                with self.assertRaisesRegex(UnionError, "decrypt_failed"):
                    refresh(public, private, config, r / "identity", STAMP, tmp)
            self.assertEqual(list(tmp.iterdir()), [])
            self.assertEqual(list(public.iterdir()), [])

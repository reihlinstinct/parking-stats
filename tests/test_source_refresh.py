import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parking_stats.source_refresh import (
    artifact_gate,
    diff_gate,
    freshness,
    refresh,
    semantic,
)
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
                    "parking_stats.source_refresh.decrypt",
                    side_effect=UnionError("decrypt_failed"),
                ),
            ):
                with self.assertRaisesRegex(UnionError, "decrypt_failed"):
                    refresh(public, private, config, r / "identity", STAMP, tmp)
            self.assertEqual(list(tmp.iterdir()), [])
            self.assertEqual(list(public.iterdir()), [])

    def fixture(self, root):
        import hashlib

        public, private, tmp = root / "public", root / "private", root / "tmp"
        for path in (public, private, tmp):
            path.mkdir()
        (public / "public/data").mkdir(parents=True)
        (public / "config").mkdir()
        cipher = b"synthetic ciphertext"
        plain = b"{}"
        (private / "baseline.age").write_bytes(cipher)
        spec = dict(
            cipher_path="baseline.age",
            cipher_sha256=hashlib.sha256(cipher).hexdigest(),
            plaintext_sha256=hashlib.sha256(plain).hexdigest(),
            streets=[],
            categories=[],
        )
        config = public / "config/parking-source.json"
        config.write_text(json.dumps(spec))
        payload = dict(
            schema_version=1,
            basis="recovered_case_identifiers_not_certified",
            case_identifiers=0,
            unresolved_rows=0,
            missing_opening_date=0,
            from_date=None,
            through=None,
            by_month={},
            by_street={},
            by_category={},
        )
        (public / "public/data/recovered-summary.json").write_text(json.dumps(payload))
        return public, private, tmp, config, payload, plain

    def test_success_artifact_and_semantic_noop(self):
        with tempfile.TemporaryDirectory() as t:
            public, private, tmp, config, payload, plain = self.fixture(Path(t))
            with (
                patch("parking_stats.source_refresh.diff_gate"),
                patch("parking_stats.source_refresh.decrypt", return_value=plain),
                patch(
                    "parking_stats.source_refresh.build", return_value=(payload, META)
                ),
            ):
                self.assertTrue(
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
                )
                artifact_gate(public)
                self.assertFalse(
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
                )
            self.assertEqual(list(tmp.iterdir()), [])

    def test_plaintext_hash_mismatch_prevents_build(self):
        with tempfile.TemporaryDirectory() as t:
            public, private, tmp, config, payload, plain = self.fixture(Path(t))
            with (
                patch("parking_stats.source_refresh.diff_gate"),
                patch(
                    "parking_stats.source_refresh.decrypt", return_value=b"different"
                ),
                patch(
                    "parking_stats.source_refresh.build",
                    side_effect=AssertionError("must not build"),
                ) as build,
            ):
                with self.assertRaisesRegex(UnionError, "plaintext_hash"):
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
                build.assert_not_called()
            self.assertFalse((public / "public/data/source-freshness.json").exists())

    def test_cipher_symlink_and_size_fail_before_read(self):
        with tempfile.TemporaryDirectory() as t:
            public, private, tmp, config, payload, plain = self.fixture(Path(t))
            cipher = private / "baseline.age"
            cipher.rename(private / "target")
            cipher.symlink_to(private / "target")
            with patch.object(
                Path, "read_bytes", side_effect=AssertionError("read too early")
            ) as spy:
                with self.assertRaisesRegex(UnionError, "cipher_symlink"):
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
                spy.assert_not_called()
            cipher.unlink()
            cipher.write_bytes(b"x" * (20 * 1024 * 1024 + 1))
            with patch.object(
                Path, "read_bytes", side_effect=AssertionError("read too early")
            ) as spy:
                with self.assertRaisesRegex(UnionError, "cipher_size"):
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
                spy.assert_not_called()

    def test_hostile_artifacts_and_output_privacy(self):
        with tempfile.TemporaryDirectory() as t:
            public, private, tmp, config, payload, plain = self.fixture(Path(t))
            (public / "public/data/source-freshness.json").write_text(
                json.dumps(dict(META, raw_notes="private"))
            )
            with self.assertRaises(UnionError):
                artifact_gate(public)
            bad = dict(payload, raw_notes="private")
            (public / "public/data/recovered-summary.json").write_text(json.dumps(bad))
            with self.assertRaises(UnionError):
                artifact_gate(public)
            # Inject a scanner finding after mocked build to exercise the final
            # independent candidate scan rather than only reducer validation.
            with (
                patch("parking_stats.source_refresh.diff_gate"),
                patch("parking_stats.source_refresh.decrypt", return_value=plain),
                patch(
                    "parking_stats.source_refresh.build", return_value=(payload, META)
                ),
                patch(
                    "parking_stats.source_refresh.scan_lines", return_value=[object()]
                ),
            ):
                with self.assertRaisesRegex(UnionError, "public_candidate_privacy"):
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)

    def test_stream_decrypt_caps_timeout_and_success(self):
        import sys

        from parking_stats.source_refresh import decrypt

        original = subprocess.Popen
        for script, expected, patches in (
            ("import sys;sys.stdout.write('ok')", None, {}),
            (
                "import sys;sys.stdout.write('x'*100)",
                "decrypt_output_limit",
                {"DECRYPT_OUTPUT_LIMIT": 10},
            ),
            (
                "import sys;sys.stderr.write('x'*100)",
                "decrypt_error_limit",
                {"DECRYPT_ERROR_LIMIT": 10},
            ),
            ("import time;time.sleep(1)", "decrypt_timeout", {"DECRYPT_TIMEOUT": 0.01}),
        ):

            def launch(*args, **kwargs):
                return original([sys.executable, "-c", script], **kwargs)

            with (
                patch(
                    "parking_stats.source_refresh.subprocess.Popen", side_effect=launch
                ),
                patch.dict(
                    __import__("parking_stats.source_refresh", fromlist=["x"]).__dict__,
                    patches,
                ),
            ):
                if expected:
                    with self.assertRaisesRegex(UnionError, expected):
                        decrypt(b"input", Path("unused"))
                else:
                    self.assertEqual(decrypt(b"input", Path("unused")), b"ok")

    def test_invalid_build_output_never_replaces(self):
        with tempfile.TemporaryDirectory() as t:
            public, private, tmp, config, payload, plain = self.fixture(Path(t))
            before = (public / "public/data/recovered-summary.json").read_bytes()
            with (
                patch("parking_stats.source_refresh.diff_gate"),
                patch("parking_stats.source_refresh.decrypt", return_value=plain),
                patch(
                    "parking_stats.source_refresh.build",
                    return_value=({"private_case": "synthetic"}, META),
                ),
                patch(
                    "parking_stats.source_refresh.os.replace",
                    side_effect=AssertionError("must not write"),
                ) as replace,
            ):
                with self.assertRaises(UnionError):
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
                replace.assert_not_called()
            self.assertEqual(
                (public / "public/data/recovered-summary.json").read_bytes(), before
            )

    def test_config_hash_wrong_shapes_and_extra_private_fields(self):
        with tempfile.TemporaryDirectory() as t:
            public, private, tmp, config, payload, plain = self.fixture(Path(t))
            original = json.loads(config.read_text())
            for value in ([], ["bad"], ["z" * 8] * 8, True, None):
                spec = dict(original, cipher_sha256=value)
                config.write_text(json.dumps(spec))
                with self.assertRaisesRegex(UnionError, "refresh_hash_config"):
                    refresh(public, private, config, Path(t) / "identity", STAMP, tmp)
            spec = dict(original, raw_notes="private")
            config.write_text(json.dumps(spec))
            with self.assertRaisesRegex(UnionError, "refresh_config"):
                refresh(public, private, config, Path(t) / "identity", STAMP, tmp)

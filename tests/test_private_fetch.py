import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location(
    "private_fetch", Path(__file__).parents[1] / "scripts/fetch_parking_source.py"
)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class PrivateFetchTests(unittest.TestCase):
    def test_diagnostic_no_untrusted_prose(self):
        for error in (ValueError("PRIVATE\n::warning::path"), MOD.FetchError([], {})):
            self.assertEqual(
                MOD.diagnostic(error),
                "private_source_fetch_failed stage=startup code=other",
            )
        self.assertEqual(
            MOD.diagnostic(MOD.FetchError("fetch", "timeout")),
            "private_source_fetch_failed stage=fetch code=timeout",
        )

    def test_global_deadline_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(MOD.time, "monotonic", return_value=9.9):
                MOD.check_budget(root, 10, "fetch")
            with patch.object(MOD.time, "monotonic", return_value=10):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.check_budget(root, 10, "fetch")
                self.assertEqual(error.exception.code, "timeout")

    def test_size_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file").write_bytes(b"123")
            with patch.object(MOD, "MAX_CHECKOUT_BYTES", 3):
                MOD.check_budget(root, float("inf"), "fetch")
            with patch.object(MOD, "MAX_CHECKOUT_BYTES", 2):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.check_budget(root, float("inf"), "fetch")
                self.assertEqual(error.exception.code, "size_limit")

    def test_git_timeout_kills_child_and_does_not_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = MagicMock()
            child.poll.side_effect = [None, None]
            with (
                patch.object(MOD.subprocess, "Popen", return_value=child),
                patch.object(
                    MOD,
                    "check_budget",
                    side_effect=[None, MOD.FetchError("fetch", "timeout")],
                ),
            ):
                with self.assertRaises(MOD.FetchError):
                    MOD.git(root, ["fetch"], {}, root, 10, "fetch")
            child.kill.assert_called_once()
            child.wait.assert_called_once()

    def test_nonzero_has_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = MagicMock()
            child.poll.return_value = 1
            child.returncode = 1
            with patch.object(MOD.subprocess, "Popen", return_value=child):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.git(root, ["fetch"], {}, root, float("inf"), "fetch")
            self.assertEqual(error.exception.code, "nonzero_exit")

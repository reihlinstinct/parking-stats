import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parking_stats.private_builder import Reader
from parking_stats.source_refresh import _STAGE, STAGES, diagnostic, main
from parking_stats.union import UnionError


class DiagnosticTests(unittest.TestCase):
    def test_input_errors_not_conflated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(UnionError, "^input_read$"):
                Reader(root).obj("missing.json")
            (root / "bad.json").write_text("{broken")
            with self.assertRaisesRegex(UnionError, "^input_json$"):
                Reader(root).obj("bad.json")
            (root / "dup.json").write_text('{"a":1,"a":2}')
            with self.assertRaisesRegex(UnionError, "^duplicate_json_key$"):
                Reader(root).obj("dup.json")

    def test_hostile_messages_and_stage_never_echoed(self):
        secret = "PRIVATE-value\n::warning::leak /private/path"
        for error in (
            UnionError(secret),
            ValueError(secret),
            OSError(secret),
            UnionError("input_read", secret),
            UnionError(["input_read"]),
            RuntimeError(secret),
        ):
            for stage in ("private_build", secret, None, []):
                text = diagnostic(error, stage)
                self.assertNotIn(secret, text)
                self.assertNotIn("PRIVATE", text)
                self.assertNotIn("/private", text)
                self.assertLess(len(text), 150)
                self.assertEqual(len(text.splitlines()), 1)

    def test_codes_and_stages_exact(self):
        for stage in STAGES:
            self.assertEqual(
                diagnostic(UnionError("input_read"), stage),
                "source_refresh_failed_closed stage=" + stage + " code=input_read",
            )
        self.assertIn(
            "code=unexpected_error",
            diagnostic(UnionError("input_read\nprivate"), "private_build"),
        )

    def test_main_prints_value_free_stage_code(self):
        def fail(*args, **kwargs):
            _STAGE.set("private_build")
            raise UnionError("input_read")

        argv = ["refresh"]
        for name in (
            "public",
            "private",
            "config",
            "identity",
            "temporary",
            "baseline",
        ):
            argv += ["--" + name, "unused"]
        argv += ["--observed", "2026-11-04T12:00:00+02:00"]
        err = io.StringIO()
        with (
            patch("sys.argv", argv),
            patch("parking_stats.source_refresh.refresh", side_effect=fail),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(main(), 1)
        self.assertEqual(
            err.getvalue(),
            "source_refresh_failed_closed stage=private_build code=input_read\n",
        )

    def test_main_unexpected_types_and_hostile_subclass_end_to_end(self):
        import subprocess

        class Hostile(UnionError):
            def __str__(self):
                return "PRIVATE subclass traceback content"

        class HostileString(str):
            def __str__(self):
                return "PRIVATE custom string"

        argv = ["refresh"]
        for name in (
            "public",
            "private",
            "config",
            "identity",
            "temporary",
            "baseline",
        ):
            argv += ["--" + name, "unused"]
        argv += ["--observed", "2026-11-04T12:00:00+02:00"]
        cases = [
            (Exception("PRIVATE general"), "unexpected_error"),
            (OSError("PRIVATE path"), "system_io_error"),
            (subprocess.SubprocessError("PRIVATE command"), "subprocess_error"),
            (Hostile("input_read"), "unexpected_error"),
            (UnionError(HostileString("input_read")), "unexpected_error"),
            (UnionError(object()), "unexpected_error"),
        ]
        for error, code in cases:
            with self.subTest(kind=type(error).__name__):

                def fail(*args, **kwargs):
                    _STAGE.set("private_build")
                    raise error

                out, err = io.StringIO(), io.StringIO()
                with (
                    patch("sys.argv", argv),
                    patch("parking_stats.source_refresh.refresh", side_effect=fail),
                    contextlib.redirect_stdout(out),
                    contextlib.redirect_stderr(err),
                ):
                    self.assertEqual(main(), 1)
                self.assertEqual(out.getvalue(), "")
                self.assertEqual(
                    err.getvalue(),
                    "source_refresh_failed_closed stage=private_build code="
                    + code
                    + "\n",
                )
                self.assertNotIn("Traceback", err.getvalue())
                self.assertNotIn("PRIVATE", err.getvalue())

    def test_diagnostic_exact_types_for_custom_str_and_subclass(self):
        class Custom(str):
            def __str__(self):
                raise AssertionError("must never stringify")

        class Derived(UnionError):
            pass

        for error in (Derived("input_read"), UnionError(Custom("input_read"))):
            self.assertEqual(
                diagnostic(error, "private_build"),
                "source_refresh_failed_closed stage=private_build code=unexpected_error",
            )
        self.assertEqual(
            diagnostic(UnionError("input_read"), Custom("private_build")),
            "source_refresh_failed_closed stage=startup code=input_read",
        )

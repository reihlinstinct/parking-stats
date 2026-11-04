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
                patch.object(MOD.os, "killpg"),
                patch.object(MOD.subprocess, "Popen", return_value=child),
                patch.object(
                    MOD,
                    "check_budget",
                    side_effect=[None, MOD.FetchError("fetch", "timeout")],
                ),
            ):
                with self.assertRaises(MOD.FetchError):
                    MOD.git(root, ["fetch"], {}, root, 10, "fetch")
            child.wait.assert_called_once()

    def test_nonzero_has_fixed_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = MagicMock()
            child.poll.return_value = 1
            child.returncode = 1
            with (
                patch.object(MOD.os, "killpg"),
                patch.object(MOD.subprocess, "Popen", return_value=child),
            ):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.git(root, ["fetch"], {}, root, float("inf"), "fetch")
            self.assertEqual(error.exception.code, "nonzero_exit")

    def test_hostile_stage_code_exact_types(self):
        class Custom(str):
            def __str__(self):
                raise AssertionError("must never stringify")

        for value in (
            "PRIVATE/path",
            "fetch\n::warning::PRIVATE",
            "::PRIVATE",
            Custom("fetch"),
            None,
            [],
        ):
            text = MOD.diagnostic(MOD.FetchError(value, "timeout"))
            self.assertEqual(
                text, "private_source_fetch_failed stage=startup code=timeout"
            )
        for value in (
            "PRIVATE/path",
            "timeout\n::warning::PRIVATE",
            "::PRIVATE",
            Custom("timeout"),
            None,
            [],
        ):
            text = MOD.diagnostic(MOD.FetchError("fetch", value))
            self.assertEqual(text, "private_source_fetch_failed stage=fetch code=other")

        class Derived(MOD.FetchError):
            pass

        self.assertEqual(
            MOD.diagnostic(Derived("fetch", "timeout")),
            "private_source_fetch_failed stage=startup code=other",
        )

    def test_main_all_calls_share_one_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [0.0]
            deadlines = []
            stages = []

            def fake_git(path, args, env, root, deadline, stage, capture=False):
                deadlines.append(deadline)
                stages.append(stage)
                self.assertEqual(deadline, 300)
                MOD.check_budget(root, deadline, stage)
                clock[0] += 45
                if stage == "revision":
                    return b"a" * 40 + b"\n"
                if stage == "initialize":
                    (path / ".git").mkdir()
                return b""

            with (
                patch.dict(
                    MOD.os.environ,
                    {"RUNNER_TEMP": tmp, "PARKING_SOURCE_READ_TOKEN": "synthetic"},
                ),
                patch.object(MOD.time, "monotonic", side_effect=lambda: clock[0]),
                patch.object(MOD, "git", side_effect=fake_git),
                patch("sys.stdout"),
            ):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.main()
            self.assertEqual(error.exception.code, "timeout")
            self.assertEqual(
                stages, ["initialize", "fetch", "revision", "checkout"] * 2
            )
            self.assertEqual(set(deadlines), {300})
            self.assertFalse((Path(tmp) / "parking-private").exists())

    def test_timeout_kills_real_descendant_process_group(self):
        import os
        import sys
        import time

        original = MOD.subprocess.Popen
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / "descendant.pid"
            script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);open(sys.argv[1],'w').write(str(p.pid));time.sleep(30)"

            def launch(*args, **kwargs):
                return original([sys.executable, "-c", script, str(pidfile)], **kwargs)

            deadline = time.monotonic() + 0.5
            with patch.object(MOD.subprocess, "Popen", side_effect=launch):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.git(root, ["fetch"], os.environ.copy(), root, deadline, "fetch")
            self.assertEqual(error.exception.code, "timeout")
            pid = int(pidfile.read_text())
            # A killed orphan can briefly remain a zombie until init reaps it;
            # it must not be running after the parent cleanup returns.
            for _ in range(50):
                status = Path(f"/proc/{pid}/stat")
                if not status.exists() or status.read_text().split()[2] == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("descendant survived timeout")

    def test_git_multicall_deadline_is_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clock = [0.0]
            deadline = 300

            def child(*args, **kwargs):
                process = MagicMock()
                process.pid = 12345
                process.returncode = 0

                def poll():
                    clock[0] += 60
                    return 0

                process.poll.side_effect = poll
                return process

            with (
                patch.object(MOD.time, "monotonic", side_effect=lambda: clock[0]),
                patch.object(MOD.subprocess, "Popen", side_effect=child),
                patch.object(MOD.os, "killpg"),
            ):
                MOD.git(root, ["init"], {}, root, deadline, "initialize")
                MOD.git(root, ["fetch"], {}, root, deadline, "fetch")
                MOD.git(root, ["revision"], {}, root, deadline, "revision")
                MOD.git(root, ["checkout"], {}, root, deadline, "checkout")
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.git(root, ["checkout"], {}, root, deadline, "checkout")
            self.assertEqual(error.exception.code, "timeout")

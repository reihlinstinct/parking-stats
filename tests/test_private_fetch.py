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


def _process_gone_or_zombie(status):
    # /proc can disappear when init reaps an orphan between polling steps.
    try:
        return status.read_text().split()[2] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        # ESRCH: the process exited between the poll and the read.
        return True


class ProcessStatusTests(unittest.TestCase):
    def test_reaped_process_is_gone(self):
        status = MagicMock()
        status.read_text.side_effect = FileNotFoundError
        self.assertTrue(_process_gone_or_zombie(status))

    def test_exit_during_read_is_gone(self):
        status = MagicMock()
        status.read_text.side_effect = ProcessLookupError
        self.assertTrue(_process_gone_or_zombie(status))

    def test_zombie_is_gone_but_live_process_is_not(self):
        for state, expected in (("Z", True), ("S", False), ("R", False)):
            with self.subTest(state=state):
                status = MagicMock()
                status.read_text.return_value = f"123 (python) {state} 1"
                self.assertEqual(_process_gone_or_zombie(status), expected)

    def test_other_read_errors_are_not_hidden(self):
        status = MagicMock()
        status.read_text.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            _process_gone_or_zombie(status)


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

    def test_git_transient_file_removed_during_scan_is_safe(self):
        # Reproduce Git's pack/lock rename between os.walk and lstat.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transient = root / "current/.git/objects/pack/tmp_pack_synthetic"
            transient.parent.mkdir(parents=True)
            transient.write_bytes(b"pack")
            original = Path.lstat

            def disappear(path, *args, **kwargs):
                if path == transient:
                    MOD.os.unlink(transient)
                    raise FileNotFoundError(2, "PRIVATE/token/path")
                return original(path, *args, **kwargs)

            with patch.object(Path, "lstat", disappear):
                MOD.check_budget(root, float("inf"), "checkout")

    def test_missing_materialized_source_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "report.json"
            source.write_bytes(b"{}")
            with patch.object(Path, "lstat", side_effect=FileNotFoundError):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.check_budget(root, float("inf"), "checkout")
            self.assertEqual(
                MOD.diagnostic(error.exception),
                "private_source_fetch_failed stage=checkout code=filesystem_missing",
            )

    def test_fixed_error_classification_never_prints_exception(self):
        import errno

        for number, expected in (
            (errno.ENOENT, "filesystem_missing"),
            (errno.EACCES, "filesystem_denied"),
            (errno.ENOSPC, "filesystem_full"),
            (errno.EIO, "filesystem_io"),
        ):
            error = OSError(number, "PRIVATE\n::warning::token", "/private/path")
            self.assertEqual(
                MOD.diagnostic(MOD.classify_error("checkout", error)),
                "private_source_fetch_failed stage=checkout code=" + expected,
            )
        error = ValueError("PRIVATE\n::warning::token")
        self.assertEqual(
            MOD.diagnostic(MOD.classify_error("checkout", error)),
            "private_source_fetch_failed stage=checkout code=internal_error",
        )

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

            def fake_git(
                path, args, env, root, deadline, stage, capture=False, max_output=1024
            ):
                deadlines.append(deadline)
                stages.append(stage)
                self.assertEqual(deadline, 300)
                MOD.check_budget(root, deadline, stage)
                clock[0] += 25
                if stage == "checkout" and args[0] == "ls-tree":
                    return (
                        b"100644 blob " + b"b" * 40 + b"\t" + args[-1].encode() + b"\0"
                    )
                if stage == "checkout":
                    return (
                        b"folder_id,metadata_record,ref_mapping\nrow,reports/row/report.json,verified\n"
                        if args[-1].endswith(":ledger/reports.csv")
                        else b"{}"
                    )
                if stage == "revision":
                    return b"a" * 40 + b"\n"
                if args[0] == "init":
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
            self.assertEqual(len(stages), 12)
            self.assertIn("checkout", stages)
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
                if _process_gone_or_zombie(status):
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


class NarrowFetchTests(unittest.TestCase):
    def test_required_paths_only_exact_json_members(self):
        self.assertEqual(
            MOD.required_paths(
                b"folder_id,metadata_record,ref_mapping\nrow,reports/row/report.json,verified\nprep,../../no,not_filed_prepared\nempty,,pending\n"
            ),
            ["reports/row/report.json"],
        )
        for path in (
            "../secret",
            "/secret",
            "reports/other/report.json",
            "ledger/report.json",
            "reports/row/report.txt",
            "reports/row/original.jpg",
        ):
            raw = (
                "folder_id,metadata_record,ref_mapping\nrow," + path + ",verified\n"
            ).encode()
            with self.assertRaises(MOD.FetchError) as error:
                MOD.required_paths(raw)
            self.assertEqual(error.exception.code, "source_path")

    def test_fake_fetch_never_checkout_or_photo_and_missing_path_fixed(self):
        for missing in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                calls = []

                def fake_git(
                    path,
                    args,
                    env,
                    root,
                    deadline,
                    stage,
                    capture=False,
                    max_output=1024,
                ):
                    calls.append((args, stage, deadline))
                    if args[0] == "init":
                        (path / ".git").mkdir()
                    if stage == "fetch":
                        self.assertIn("--filter=blob:none", args)
                    if stage == "revision":
                        return b"a" * 40 + b"\n"
                    if stage == "checkout" and args[0] == "ls-tree":
                        if missing and args[-1] != "ledger/reports.csv":
                            return b""
                        return (
                            b"100644 blob "
                            + b"b" * 40
                            + b"\t"
                            + args[-1].encode()
                            + b"\0"
                        )
                    if stage == "checkout":
                        if args[-1].endswith(":ledger/reports.csv"):
                            return b"folder_id,metadata_record,ref_mapping\nrow,reports/row/report.json,verified\n"
                        if missing:
                            raise MOD.FetchError("checkout", "nonzero_exit")
                        return b"{}"
                    return b""

                with (
                    patch.dict(
                        MOD.os.environ,
                        {"RUNNER_TEMP": tmp, "PARKING_SOURCE_READ_TOKEN": "synthetic"},
                    ),
                    patch.object(MOD, "git", side_effect=fake_git),
                    patch("sys.stdout"),
                ):
                    if missing:
                        with self.assertRaises(MOD.FetchError) as error:
                            MOD.main()
                        self.assertEqual(
                            (error.exception.stage, error.exception.code),
                            ("checkout", "missing_path"),
                        )
                        self.assertFalse((Path(tmp) / "parking-private").exists())
                    else:
                        MOD.main()
                        root = Path(tmp) / "parking-private"
                        self.assertEqual(
                            sorted(
                                str(p.relative_to(root))
                                for p in root.rglob("*")
                                if p.is_file()
                            ),
                            [
                                "baseline/private/recovered-cases/union.json.age",
                                "current/ledger/reports.csv",
                                "current/ledger/stats-city-events.json",
                                "current/reports/row/report.json",
                            ],
                        )
                for args, stage, deadline in calls:
                    self.assertNotIn("checkout", args)
                    self.assertFalse(
                        any(str(a).endswith((".jpg", ".png", ".py")) for a in args)
                    )
                self.assertEqual(len({d for a, s, d in calls}), 1)

    def test_lazy_blob_os_error_keeps_stage_and_cleans_private_data(self):
        import errno

        with tempfile.TemporaryDirectory() as tmp:

            def fake_git(
                path, args, env, root, deadline, stage, capture=False, max_output=1024
            ):
                if args[0] == "init":
                    (path / ".git").mkdir()
                if stage == "revision":
                    return b"a" * 40 + b"\n"
                if stage == "checkout" and args[0] == "ls-tree":
                    return (
                        b"100644 blob " + b"b" * 40 + b"\t" + args[-1].encode() + b"\0"
                    )
                if stage == "checkout":
                    raise OSError(errno.ENOSPC, "PRIVATE/token/path")
                return b""

            with (
                patch.dict(
                    MOD.os.environ,
                    {"RUNNER_TEMP": tmp, "PARKING_SOURCE_READ_TOKEN": "synthetic"},
                ),
                patch.object(MOD, "git", side_effect=fake_git),
                patch("sys.stdout"),
            ):
                with self.assertRaises(MOD.FetchError) as error:
                    MOD.main()
            self.assertEqual(
                MOD.diagnostic(error.exception),
                "private_source_fetch_failed stage=checkout code=filesystem_full",
            )
            self.assertFalse((Path(tmp) / "parking-private").exists())


class RealNarrowFetchTests(unittest.TestCase):
    def test_real_git_materializes_only_selected_blobs(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote = root / "remote.git"
            seed = root / "seed"
            runroot = root / "run"
            runroot.mkdir()

            def command(*args, cwd=None):
                return subprocess.run(
                    ["git", *args], cwd=cwd, capture_output=True, check=True, text=True
                ).stdout.strip()

            command("init", "--bare", str(remote))
            command("config", "uploadpack.allowFilter", "true", cwd=remote)
            command("clone", str(remote), str(seed))
            command("config", "user.email", "synthetic@example.invalid", cwd=seed)
            command("config", "user.name", "Synthetic", cwd=seed)
            command("checkout", "-b", "main", cwd=seed)
            (seed / "ledger").mkdir()
            (seed / "ledger/reports.csv").write_text(
                "folder_id,metadata_record,ref_mapping\nrow,reports/row/report.json,verified\n"
            )
            (seed / "reports/row").mkdir(parents=True)
            (seed / "reports/row/report.json").write_text("{}")
            (seed / "reports/row/original.jpg").write_bytes(
                b"PRIVATE-SYNTHETIC-PHOTO" * 100
            )
            (seed / "private/recovered-cases").mkdir(parents=True)
            (seed / "private/recovered-cases/union.json.age").write_bytes(
                b"synthetic-cipher"
            )
            command("add", ".", cwd=seed)
            command("commit", "-m", "Synthetic fixture", cwd=seed)
            command("push", "origin", "main", cwd=seed)
            sha = command("rev-parse", "HEAD", cwd=seed)
            original = MOD.git

            def local_git(
                path, args, env, root, deadline, stage, capture=False, max_output=1024
            ):
                args = list(args)
                args = [
                    str(remote)
                    if a == "https://github.com/reihlinstinct/parking-reports.git"
                    else a
                    for a in args
                ]
                args = [
                    sha if a == "79db451eecba2fb61aa3ee449f866cc205010b3b" else a
                    for a in args
                ]
                return original(
                    path, args, env, root, deadline, stage, capture, max_output
                )

            with (
                patch.dict(
                    MOD.os.environ,
                    {
                        "RUNNER_TEMP": str(runroot),
                        "PARKING_SOURCE_READ_TOKEN": "synthetic",
                    },
                ),
                patch.object(MOD, "git", side_effect=local_git),
                patch("sys.stdout"),
            ):
                MOD.main()
            actual = sorted(
                str(p.relative_to(runroot / "parking-private"))
                for p in (runroot / "parking-private").rglob("*")
                if p.is_file()
            )
            self.assertEqual(
                actual,
                [
                    "baseline/private/recovered-cases/union.json.age",
                    "current/ledger/reports.csv",
                    "current/reports/row/report.json",
                ],
            )
            self.assertFalse(
                any(p.name == ".git" for p in (runroot / "parking-private").rglob("*"))
            )


class MaliciousObjectTests(unittest.TestCase):
    def test_git_tree_modes_rejected(self):
        for mode, kind in [("120000", "blob"), ("100755", "blob"), ("040000", "tree")]:
            with tempfile.TemporaryDirectory() as tmp:

                def fake(
                    path,
                    args,
                    env,
                    root,
                    deadline,
                    stage,
                    capture=False,
                    max_output=1024,
                ):
                    if stage == "checkout" and args[0] != "ls-tree":
                        raise AssertionError("unsafe blob was fetched")
                    if args[0] == "init":
                        (path / ".git").mkdir()
                    if stage == "revision":
                        return b"a" * 40 + b"\n"
                    if stage == "checkout" and args[0] == "ls-tree":
                        return (
                            mode + " " + kind + " " + "b" * 40 + "\t" + args[-1] + "\0"
                        ).encode()
                    return b""

                with (
                    patch.dict(
                        MOD.os.environ,
                        {"RUNNER_TEMP": tmp, "PARKING_SOURCE_READ_TOKEN": "synthetic"},
                    ),
                    patch.object(MOD, "git", side_effect=fake),
                    patch("sys.stdout"),
                ):
                    with self.assertRaises(MOD.FetchError) as error:
                        MOD.main()
                    self.assertEqual(error.exception.code, "source_path")

"""Hold marker and run-boundary guards, offline fixtures only (#36)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from parking_stats.publish_guard import (
    BLOCKED_MESSAGE,
    HOLD_MARKER,
    main,
    read_main_sha,
    read_marker,
    require_publish_allowed,
    verify_main_unchanged,
)
from parking_stats.union import UnionError

GOOD_MARKER = {
    "schema_version": 1,
    "hold": True,
    "since": "2026-11-18",
    "note": "Publication paused by the owner.",
}


def write_marker(root: Path, payload: object) -> None:
    path = root / HOLD_MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "seed.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    sha = (
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    subprocess.run(
        ["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", sha],
        check=True,
    )
    return sha


class HoldMarkerTests(unittest.TestCase):
    def test_no_marker_allows_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            require_publish_allowed(Path(tmp))
            self.assertIsNone(read_marker(Path(tmp)))

    def test_active_hold_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), GOOD_MARKER)
            with self.assertRaises(UnionError) as ctx:
                require_publish_allowed(Path(tmp))
            self.assertEqual(ctx.exception.args, ("hold_active",))

    def test_cleared_hold_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), GOOD_MARKER | {"hold": False})
            require_publish_allowed(Path(tmp))

    def test_malformed_markers_fail_closed(self):
        bad = [
            "not json",
            json.dumps({"schema_version": 1, "hold": True, "since": "2026-11-18"}),
            json.dumps(GOOD_MARKER | {"extra": 1}),
            json.dumps(GOOD_MARKER | {"schema_version": 2}),
            json.dumps(GOOD_MARKER | {"schema_version": "1"}),
            json.dumps(GOOD_MARKER | {"hold": "yes"}),
            json.dumps(GOOD_MARKER | {"since": "18.11.2026"}),
            json.dumps(GOOD_MARKER | {"since": "2026-13-01"}),
            json.dumps(GOOD_MARKER | {"note": "x" * 201}),
            json.dumps(GOOD_MARKER | {"note": 42}),
        ]
        for payload in bad:
            with tempfile.TemporaryDirectory() as tmp:
                write_marker(Path(tmp), payload)
                with self.assertRaises(UnionError) as ctx:
                    require_publish_allowed(Path(tmp))
                self.assertIn(ctx.exception.args[0], ("hold_marker_schema",))

    def test_marker_with_private_canary_fails_closed_as_privacy(self):
        # The phone-pattern canary is assembled at runtime so the literal
        # never appears in source (the repo privacy scan covers tests too).
        canary = "call 0" + "52-12" + "34567 about the case"
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), GOOD_MARKER | {"note": canary})
            with self.assertRaises(UnionError) as ctx:
                require_publish_allowed(Path(tmp))
            self.assertEqual(ctx.exception.args, ("hold_marker_privacy",))

    def test_clean_active_marker_blocks_as_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), GOOD_MARKER | {"note": "paused by the owner"})
            with self.assertRaises(UnionError) as ctx:
                require_publish_allowed(Path(tmp))
            self.assertEqual(ctx.exception.args, ("hold_active",))

    def test_invalid_utf8_marker_fails_closed_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / HOLD_MARKER
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xfe not utf-8")
            with self.assertRaises(UnionError) as ctx:
                require_publish_allowed(Path(tmp))
            self.assertEqual(ctx.exception.args, ("hold_marker_schema",))
            import io
            from contextlib import redirect_stdout

            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["check-hold", "--public-root", tmp])
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue().strip(), BLOCKED_MESSAGE)

    def test_case_identifier_like_note_is_not_privacy_caught(self):
        # Pins the narrowed privacy claim: the scanner's rules do not detect
        # municipal case-identifier-like strings, so such a note blocks as
        # hold_active, not hold_marker_privacy. The marker note must stay a
        # fixed generic owner message; the scan is not its only safeguard.
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), GOOD_MARKER | {"note": "see case 106/1234567"})
            with self.assertRaises(UnionError) as ctx:
                require_publish_allowed(Path(tmp))
            self.assertEqual(ctx.exception.args, ("hold_active",))

    def test_oversized_marker_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), json.dumps(GOOD_MARKER) + " " * 5000)
            with self.assertRaises(UnionError):
                require_publish_allowed(Path(tmp))


class RunBoundaryTests(unittest.TestCase):
    def test_unchanged_main_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sha = init_repo(Path(tmp))
            self.assertEqual(read_main_sha(Path(tmp)), sha)
            verify_main_unchanged(Path(tmp), sha)

    def test_moved_main_aborts(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(Path(tmp))
            (Path(tmp) / "seed.txt").write_text("moved", encoding="utf-8")
            subprocess.run(["git", "-C", tmp, "add", "seed.txt"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    tmp,
                    "-c",
                    "user.email=t@t",
                    "-c",
                    "user.name=t",
                    "commit",
                    "-q",
                    "-m",
                    "move",
                ],
                check=True,
            )
            new = (
                subprocess.run(
                    ["git", "-C", tmp, "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                )
                .stdout.decode()
                .strip()
            )
            subprocess.run(
                ["git", "-C", tmp, "update-ref", "refs/remotes/origin/main", new],
                check=True,
            )
            with self.assertRaises(UnionError) as ctx:
                verify_main_unchanged(Path(tmp), "0" * 40)
            self.assertEqual(ctx.exception.args, ("main_moved",))

    def test_bad_expected_sha_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(Path(tmp))
            with self.assertRaises(UnionError):
                verify_main_unchanged(Path(tmp), "not-a-sha")


class CliTests(unittest.TestCase):
    def test_check_hold_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(main(["check-hold", "--public-root", tmp]), 0)
            write_marker(Path(tmp), GOOD_MARKER)
            self.assertEqual(main(["check-hold", "--public-root", tmp]), 1)

    def test_verify_main_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            sha = init_repo(Path(tmp))
            self.assertEqual(main(["verify-main", "--repo", tmp, "--expected", sha]), 0)
            self.assertEqual(
                main(["verify-main", "--repo", tmp, "--expected", "0" * 40]), 1
            )

    def test_blocked_output_is_generic(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_marker(Path(tmp), GOOD_MARKER)
            import io
            from contextlib import redirect_stdout

            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["check-hold", "--public-root", tmp])
            self.assertEqual(code, 1)
            self.assertEqual(out.getvalue().strip(), BLOCKED_MESSAGE)
            self.assertNotIn("paused", out.getvalue())


if __name__ == "__main__":
    unittest.main()

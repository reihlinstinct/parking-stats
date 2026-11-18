"""Executable offline form of the design's named acceptance tests (#36).

Named test 1 (revoked token) is covered by test_source_refresh's decrypt
failure tests; named test 4 (stale-source display) by pipeline-health.test.cjs;
named test 5 (monthly snapshot month) by test_monthly.py. This file composes
the real publish_guard against disposable git repositories for named tests 2
(concurrency and run boundaries) and 3 (rollback and hold).
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from parking_stats.publish_guard import (
    HOLD_MARKER,
    read_main_sha,
    require_publish_allowed,
    verify_main_unchanged,
)
from parking_stats.source_refresh import diagnostic
from parking_stats.union import UnionError

AGGREGATES = "public/data/recovered-summary.json"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode())
    return result.stdout.decode().strip()


def commit(root: Path, name: str, content: str, message: str) -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    git(root, "add", name)
    git(
        root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", message
    )
    sha = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", sha)
    return sha


def init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return commit(root, "seed.txt", "seed", "seed")


class ConcurrencyAndRunBoundaryTests(unittest.TestCase):
    """Named test 2: one publish commit, mid-run movement aborts, latest wins."""

    def test_mid_run_main_movement_aborts_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            recorded = read_main_sha(root)  # run A starts and records main
            # A protected-path commit (restore or hold-clear) lands mid-run.
            commit(root, AGGREGATES, '{"v": "restored"}', "restore")
            with self.assertRaises(UnionError) as ctx:
                verify_main_unchanged(root, recorded)
            self.assertEqual(ctx.exception.args, ("main_moved",))
            # The abort wrote nothing: no publish commit beyond the restore.
            self.assertEqual(git(root, "log", "--oneline").count("\n") + 1, 2)
            # The pending run B re-reads at start, so it records the NEW main,
            # verifies cleanly and publishes exactly one commit with the
            # newer source content.
            recorded_b = read_main_sha(root)
            verify_main_unchanged(root, recorded_b)  # boundary check before write
            commit(root, AGGREGATES, '{"v": "newer-source"}', "publish")
            self.assertEqual(
                json.loads((root / AGGREGATES).read_text())["v"], "newer-source"
            )
            self.assertEqual(git(root, "log", "--oneline").count("\n") + 1, 3)


class RollbackAndHoldTests(unittest.TestCase):
    """Named test 3: restore is byte-identical; hold blocks all publication."""

    def test_rollback_and_hold_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_repo(root)
            lkg = '{"schema_version": 1, "case_identifiers": 10}'
            commit(root, AGGREGATES, lkg, "publish good aggregates")
            commit(root, AGGREGATES, '{"bad": true}', "bad aggregates go live")
            # Restore last-known-good through the protected path; bytes are
            # byte-identical and Pages would deploy that exact revision.
            commit(root, AGGREGATES, lkg, "restore last-known-good")
            self.assertEqual((root / AGGREGATES).read_text(), lkg)
            # Place the hold: scheduled runs and manual dispatch both block.
            commit(
                root,
                HOLD_MARKER,
                json.dumps(
                    {
                        "schema_version": 1,
                        "hold": True,
                        "since": "2026-11-18",
                        "note": "Publication paused by the owner.",
                    }
                ),
                "place hold",
            )
            for ref_mode in (False, True):
                with self.assertRaises(UnionError) as ctx:
                    if ref_mode:
                        require_publish_allowed(root, root, "origin/main")
                    else:
                        require_publish_allowed(root)
                self.assertEqual(ctx.exception.args, ("hold_active",))
            # A run exiting on the marker is not a healthy cycle: the guard
            # exits non-zero with the fixed generic string (see CliTests), so
            # it never refreshes the last-success signal.
            # Clear the hold; a scheduled run publishes again normally.
            commit(
                root,
                HOLD_MARKER,
                json.dumps(
                    {
                        "schema_version": 1,
                        "hold": False,
                        "since": "2026-11-18",
                        "note": "Publication paused by the owner.",
                    }
                ),
                "clear hold",
            )
            require_publish_allowed(root)
            require_publish_allowed(root, root, "origin/main")


class GenericDiagnosticTests(unittest.TestCase):
    """Named test 1's log contract: fixed enumerated output, never prose."""

    def test_diagnostic_is_fixed_and_generic(self):
        text = diagnostic(UnionError("decrypt_failed"), "decrypt")
        self.assertEqual(
            text, "source_refresh_failed_closed stage=decrypt code=decrypt_failed"
        )
        self.assertNotIn("revoked", text)
        unknown = diagnostic(
            UnionError("not a real code with secret 106/12345"), "decrypt"
        )
        self.assertEqual(
            unknown, "source_refresh_failed_closed stage=decrypt code=unexpected_error"
        )
        self.assertNotIn("106/12345", unknown)
        odd_stage = diagnostic(UnionError("decrypt_failed"), "../../etc")
        self.assertEqual(
            odd_stage, "source_refresh_failed_closed stage=startup code=decrypt_failed"
        )


if __name__ == "__main__":
    unittest.main()

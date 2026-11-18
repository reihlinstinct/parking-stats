"""Publication hold marker and run-boundary guards (#36).

A hold marker file in the PUBLIC repository blocks all publication, scheduled
or manual, while it exists. The publisher checks it at filter start, inside the
publish job, and immediately before any write, exiting with a fixed generic
message and a non-zero status, so a blocked or aborted run never concludes
success and never refreshes the last-success signal. The marker is committed
only through the protected path (restore / hold-clear), never by the publisher,
and carries no private source SHAs. Blocking applies only while the marker
exists: clearance fully lifts the block.

Privacy scope, stated narrowly: the marker note is scanned with the repository
privacy rules (phone and raw-export key/column patterns). Those rules do not
detect municipal case-identifier-like strings, so the note must stay a fixed
generic owner message - never source-derived or per-incident text.

Run boundary: the publisher records the public main SHA at run start and
re-reads it immediately before publishing. If main moved - for example a
protected-path restore or hold-clear landed - the run aborts rather than
rebasing. Pushes are never forced.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from .privacy_scan import scan_lines
from .union import UnionError

REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.-]{0,99}")
HOLD_MARKER = "public/data/hold.json"
HOLD_KEYS = frozenset({"schema_version", "hold", "since", "note"})
NOTE_LIMIT = 200
MAIN_SHA_RE = re.compile(r"[0-9a-f]{40}")

# Fixed generic output for every blocked/aborted exit: no marker contents, no
# stage detail, no source values, matching the trust-boundary logging rules.
BLOCKED_MESSAGE = "publication_blocked"


def read_marker(public_root: Path) -> dict[str, object] | None:
    """Return the validated hold marker, or None when no marker file exists.

    A marker that exists but cannot be validated fails closed: publication is
    blocked, because an unevaluable marker cannot prove the hold is absent.
    """
    path = public_root / HOLD_MARKER
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise UnionError("hold_marker_read") from None
    except UnicodeDecodeError:
        # Invalid UTF-8 fails closed with the generic message; an uncaught
        # decode traceback could leak the marker path into public logs.
        raise UnionError("hold_marker_schema") from None
    return validate_marker(raw)


def read_marker_from_ref(repo: Path, ref: str) -> dict[str, object] | None:
    """Validated hold marker as committed on a git ref (e.g. origin/main).

    Used by the publish job so a hold that landed after the run started is
    still caught before any write. An absent marker on a resolved ref means
    no hold. A ref that does not resolve - missing ref or broken repository -
    fails closed, because it cannot prove the hold is absent.
    """
    if REF_RE.fullmatch(ref) is None or ".." in ref:
        raise UnionError("public_git")
    resolved = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{ref}^{{commit}}",
        ],
        capture_output=True,
        check=False,
    )
    if resolved.returncode:
        # Missing ref or repo failure: distinct from an absent marker.
        raise UnionError("public_git")
    exists = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{ref}:{HOLD_MARKER}"],
        capture_output=True,
        check=False,
    )
    if exists.returncode:
        return None
    show = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{HOLD_MARKER}"],
        capture_output=True,
        check=False,
    )
    if show.returncode:
        raise UnionError("hold_marker_read")
    try:
        raw = show.stdout.decode("utf-8")
    except UnicodeDecodeError:
        # Strict like the working-tree path: invalid bytes fail closed rather
        # than being replaced into something that might parse.
        raise UnionError("hold_marker_schema") from None
    return validate_marker(raw)


def validate_marker(raw: str) -> dict[str, object]:
    if len(raw) > 4096:
        raise UnionError("hold_marker_schema")
    try:
        marker = json.loads(raw)
    except json.JSONDecodeError:
        raise UnionError("hold_marker_schema") from None
    if not isinstance(marker, dict) or set(marker) != HOLD_KEYS:
        raise UnionError("hold_marker_schema")
    if type(marker["schema_version"]) is not int or marker["schema_version"] != 1:
        raise UnionError("hold_marker_schema")
    if type(marker["hold"]) is not bool:
        raise UnionError("hold_marker_schema")
    since = marker["since"]
    if type(since) is not str or len(since) != 10 or since[4] != "-" or since[7] != "-":
        raise UnionError("hold_marker_schema")
    try:
        date.fromisoformat(since)
    except ValueError:
        raise UnionError("hold_marker_schema") from None
    note = marker["note"]
    if type(note) is not str or len(note) > NOTE_LIMIT:
        raise UnionError("hold_marker_schema")
    if scan_lines(HOLD_MARKER, raw.splitlines(), set()):
        raise UnionError("hold_marker_privacy")
    return marker


def require_publish_allowed(
    public_root: Path, repo: Path | None = None, ref: str | None = None
) -> None:
    """Raise unless the hold marker is absent or cleared.

    With ``repo`` and ``ref`` the marker is read from that ref instead of the
    working tree, catching a hold committed after the checkout was made.
    """
    marker = (
        read_marker_from_ref(repo, ref)
        if repo is not None and ref is not None
        else read_marker(public_root)
    )
    if marker is not None and marker["hold"] is True:
        raise UnionError("hold_active")


def read_main_sha(repo: Path) -> str:
    """Current public main SHA as fetched into origin/main."""
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "origin/main"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise UnionError("public_git")
    sha = result.stdout.decode().strip()
    if MAIN_SHA_RE.fullmatch(sha) is None:
        raise UnionError("public_git")
    return sha


def verify_main_unchanged(repo: Path, expected: str) -> None:
    """Abort when public main moved since the run recorded it at start."""
    if MAIN_SHA_RE.fullmatch(expected) is None:
        raise UnionError("public_git")
    if read_main_sha(repo) != expected:
        raise UnionError("main_moved")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publish_guard")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-hold")
    check.add_argument("--public-root", type=Path)
    check.add_argument("--repo", type=Path)
    check.add_argument("--ref")
    record = sub.add_parser("record-main")
    record.add_argument("--repo", type=Path, required=True)
    verify = sub.add_parser("verify-main")
    verify.add_argument("--repo", type=Path, required=True)
    verify.add_argument("--expected", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check-hold":
            if (args.repo is None) != (args.ref is None):
                parser.error("check-hold needs --repo and --ref together")
            if args.public_root is None and args.repo is None:
                parser.error("check-hold needs --public-root or --repo/--ref")
            require_publish_allowed(args.public_root or Path("."), args.repo, args.ref)
            return 0
        if args.command == "record-main":
            print(read_main_sha(args.repo))
            return 0
        verify_main_unchanged(args.repo, args.expected)
        return 0
    except UnionError:
        print(BLOCKED_MESSAGE)
        return 1


if __name__ == "__main__":
    sys.exit(main())

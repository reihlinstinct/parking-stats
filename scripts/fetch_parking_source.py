"""Quiet, shallow private data fetch with a single bounded time/size budget."""

import base64
import csv
import errno
import io
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOTAL_SECONDS = 300
CITY_EVENTS = "ledger/stats-city-events.json"
MAX_CHECKOUT_BYTES = 200 * 1024 * 1024
STAGES = frozenset(
    {"startup", "initialize", "fetch", "revision", "checkout", "cleanup"}
)
CODES = frozenset(
    {
        "precondition",
        "timeout",
        "nonzero_exit",
        "size_limit",
        "revision",
        "other",
        "missing_path",
        "source_path",
        "filesystem_missing",
        "filesystem_denied",
        "filesystem_full",
        "filesystem_io",
        "internal_error",
    }
)


class FetchError(Exception):
    def __init__(self, stage, code):
        self.stage = stage if type(stage) is str and stage in STAGES else "startup"
        self.code = code if type(code) is str and code in CODES else "other"


def diagnostic(error):
    if type(error) is FetchError:
        return (
            "private_source_fetch_failed stage=" + error.stage + " code=" + error.code
        )
    return "private_source_fetch_failed stage=startup code=other"


def classify_error(stage, error):
    """Only fixed codes, never exception text, filenames, URLs or stderr."""
    if type(error) is FetchError:
        return error
    if isinstance(error, OSError):
        code = {
            errno.ENOENT: "filesystem_missing",
            errno.EACCES: "filesystem_denied",
            errno.EPERM: "filesystem_denied",
            errno.ENOSPC: "filesystem_full",
            errno.EDQUOT: "filesystem_full",
        }.get(error.errno, "filesystem_io")
        return FetchError(stage, code)
    return FetchError(stage, "internal_error")


def check_budget(root, deadline, stage):
    if time.monotonic() >= deadline:
        raise FetchError(stage, "timeout")
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                # Git atomically renames/removes transient pack/lock files while
                # this monitor walks .git. They are not materialized source data.
                if ".git" in path.relative_to(root).parts:
                    continue
                raise FetchError(stage, "filesystem_missing") from None
            except OSError as error:
                raise classify_error(stage, error) from None
            if stat.S_ISLNK(metadata.st_mode):
                raise FetchError(stage, "other")
            total += metadata.st_size
            if total > MAX_CHECKOUT_BYTES:
                raise FetchError(stage, "size_limit")


def git(path, args, env, root, deadline, stage, capture=False, max_output=1024):
    check_budget(root, deadline, stage)
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            ["git", "-C", str(path), *args],
            env=env,
            stdout=output if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                check_budget(root, deadline, stage)
                if output.tell() > max_output:
                    raise FetchError(stage, "size_limit")
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            check_budget(root, deadline, stage)
            if process.returncode:
                raise FetchError(stage, "nonzero_exit")
            if output.tell() > max_output:
                raise FetchError(stage, "size_limit")
            output.seek(0)
            return output.read(max_output + 1)
        finally:
            # Git can leave remote-https/index-pack descendants alive even when
            # its direct parent has exited. Terminate the dedicated whole group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def required_paths(raw):
    """Mirror the builder's record boundary; untrusted CSV never selects arbitrary files."""
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), strict=True)
        if (
            not reader.fieldnames
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or not {"folder_id", "metadata_record", "ref_mapping"}
            <= set(reader.fieldnames)
        ):
            raise ValueError
        paths = set()
        for index, row in enumerate(reader):
            # Surplus cells are tolerated only when every one is empty (older
            # rows carry trailing commas); a non-empty overflow is rejected,
            # mirroring the builder's ledger_overflow rule.
            extra = row.pop(None, [])
            if (
                index >= 10000
                or not isinstance(extra, list)
                or any(extra)
                or any(value is None for value in row.values())
            ):
                raise ValueError
            folder = row["folder_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", folder):
                raise ValueError
            if row["ref_mapping"] == "not_filed_prepared":
                continue
            relative = row["metadata_record"]
            if relative:
                if relative != "reports/" + folder + "/report.json":
                    raise ValueError
                paths.add(relative)
        return sorted(paths)
    except (ValueError, UnicodeError, csv.Error, TypeError):
        raise FetchError("checkout", "source_path") from None


def main():
    deadline = time.monotonic() + TOTAL_SECONDS
    token = os.environ.pop("PARKING_SOURCE_READ_TOKEN", "")
    root = Path(os.environ["RUNNER_TEMP"]) / "parking-private"
    if not token or root.exists():
        raise FetchError("startup", "precondition")
    root.mkdir(mode=0o700)
    header = base64.b64encode(("x-access-token:" + token).encode()).decode()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + header,
        GIT_TERMINAL_PROMPT="0",
        GIT_TRACE="0",
        GIT_TRACE_PACKET="0",
        GIT_CURL_VERBOSE="0",
    )
    try:
        for folder, ref in [
            ("current", "refs/heads/main"),
            ("baseline", "79db451eecba2fb61aa3ee449f866cc205010b3b"),
        ]:
            path = root / folder
            path.mkdir(mode=0o700)

            def run(stage, *args, capture=False, max_output=1024):
                try:
                    return git(
                        path, args, env, root, deadline, stage, capture, max_output
                    )
                except FetchError:
                    raise
                except Exception as error:
                    raise classify_error(stage, error) from None

            run("initialize", "init", "-q")
            run(
                "initialize",
                "remote",
                "add",
                "origin",
                "https://github.com/reihlinstinct/parking-reports.git",
            )
            run("initialize", "config", "remote.origin.promisor", "true")
            run("initialize", "config", "remote.origin.partialclonefilter", "blob:none")
            run(
                "fetch",
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "--filter=blob:none",
                "origin",
                ref,
            )
            sha = (
                run("revision", "rev-parse", "FETCH_HEAD", capture=True)
                .decode()
                .strip()
            )
            if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise FetchError("revision", "revision")
            print("::add-mask::" + sha, flush=True)

            # A partial shallow fetch downloads trees/commits only. Materialize
            # exactly ledger + validated references, never all photos or scripts.
            def materialize(relative, optional=False):
                entry = run(
                    "checkout", "ls-tree", "-z", sha, "--", relative, capture=True
                )
                if not entry:
                    if optional:
                        return None
                    raise FetchError("checkout", "missing_path")
                # Reject trees, executable files and symlinks before lazy blob fetch.
                try:
                    mode_type, name = entry.rstrip(b"\0").split(b"\t")
                    mode, kind, object_id = mode_type.split()
                    if (
                        mode != b"100644"
                        or kind != b"blob"
                        or name.decode() != relative
                    ):
                        raise ValueError
                except (ValueError, UnicodeError):
                    raise FetchError("checkout", "source_path") from None
                raw = run(
                    "checkout",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "show",
                    sha + ":" + relative,
                    capture=True,
                    max_output=20 * 1024 * 1024,
                )
                destination = path / relative
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(raw)
                except OSError as error:
                    raise classify_error("checkout", error) from None
                check_budget(root, deadline, "checkout")
                return raw

            if folder == "current":
                raw = materialize("ledger/reports.csv")
                for relative in required_paths(raw):
                    materialize(relative)
                # Independent city opening events (aggregate-safe envelope, no
                # PII); absent until the private repo first publishes one.
                materialize(CITY_EVENTS, optional=True)
            else:
                materialize("private/recovered-cases/union.json.age")
            try:
                shutil.rmtree(path / ".git")
            except OSError as error:
                raise classify_error("cleanup", error) from None
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(diagnostic(error), file=sys.stderr)
        sys.exit(1)

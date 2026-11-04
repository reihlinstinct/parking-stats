"""Quiet, shallow private data fetch with a single bounded time/size budget."""

import base64
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOTAL_SECONDS = 300
MAX_CHECKOUT_BYTES = 200 * 1024 * 1024
STAGES = frozenset(
    {"startup", "initialize", "fetch", "revision", "checkout", "cleanup"}
)
CODES = frozenset(
    {"precondition", "timeout", "nonzero_exit", "size_limit", "revision", "other"}
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


def check_budget(root, deadline, stage):
    if time.monotonic() >= deadline:
        raise FetchError(stage, "timeout")
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                raise FetchError(stage, "other")
            total += path.stat().st_size
            if total > MAX_CHECKOUT_BYTES:
                raise FetchError(stage, "size_limit")


def git(path, args, env, root, deadline, stage, capture=False):
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
                if output.tell() > 1024:
                    raise FetchError(stage, "size_limit")
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            check_budget(root, deadline, stage)
            if process.returncode:
                raise FetchError(stage, "nonzero_exit")
            if output.tell() > 1024:
                raise FetchError(stage, "size_limit")
            output.seek(0)
            return output.read(1025)
        finally:
            # Git can leave remote-https/index-pack descendants alive even when
            # its direct parent has exited. Terminate the dedicated whole group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


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

            def run(stage, *args, capture=False):
                try:
                    return git(path, args, env, root, deadline, stage, capture)
                except FetchError:
                    raise
                except Exception:
                    raise FetchError(stage, "other") from None

            run("initialize", "init", "-q")
            run(
                "fetch",
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--quiet",
                "--depth=1",
                "--no-tags",
                "https://github.com/reihlinstinct/parking-reports.git",
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
            run(
                "checkout",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "filter.lfs.smudge=",
                "-c",
                "filter.lfs.required=false",
                "checkout",
                "--quiet",
                "--detach",
                sha,
            )
            shutil.rmtree(path / ".git")
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(diagnostic(error), file=sys.stderr)
        sys.exit(1)

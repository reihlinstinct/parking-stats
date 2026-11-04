"""Quiet fixed-destination private data fetch. Run trusted main code only."""

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    token = os.environ.pop("PARKING_SOURCE_READ_TOKEN", "")
    root = Path(os.environ["RUNNER_TEMP"]) / "parking-private"
    if not token or root.exists():
        raise ValueError("fetch_precondition")
    root.mkdir(mode=0o700)
    header = base64.b64encode(("x-access-token:" + token).encode()).decode()
    # Git config arrives by environment, never argv, disk config or stdout.
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

    def git(path, *args):
        p = subprocess.run(
            ["git", "-C", str(path), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=90,
        )
        if p.returncode:
            raise ValueError("fetch_failed")
        return p.stdout

    try:
        for folder, ref in [
            ("current", "refs/heads/main"),
            ("baseline", "79db451eecba2fb61aa3ee449f866cc205010b3b"),
        ]:
            path = root / folder
            path.mkdir(mode=0o700)
            git(path, "init", "-q")
            git(
                path,
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--quiet",
                "--depth=1",
                "https://github.com/reihlinstinct/parking-reports.git",
                ref,
            )
            sha = git(path, "rev-parse", "FETCH_HEAD").decode().strip()
            if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("fetch_revision")
            print("::add-mask::" + sha, flush=True)
            git(
                path,
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
            # No private git metadata needs to survive data checkout.
            shutil.rmtree(path / ".git")
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("private_source_fetch_failed", file=sys.stderr)
        sys.exit(1)

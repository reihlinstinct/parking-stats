"""Enforce the repository layout contract in .repo-layout.yml (issue #721).

Reads .repo-layout.yml (JSON syntax, which is also valid YAML) and the
tracked file list, then fails on:
  - a missing required root file or directory (community standards);
  - a tracked top-level directory not listed in the contract;
  - a root file matching a deny pattern or no allow pattern (the contract
    denies *.py at the root: new code belongs in src/, tools/, scripts/
    or tests/, decided up front per CONTRIBUTING.md);
  - a file inside a listed directory matching none of its allow patterns.

Standard library only; no network. Usage:
  python tools/check_repo_layout.py [--root DIR]
Exit 0 when the layout conforms, 1 with one line per violation otherwise.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path


def load_contract(root: Path) -> dict:
    contract_path = root / ".repo-layout.yml"
    if not contract_path.is_file():
        raise SystemExit(f"missing layout contract: {contract_path}")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("version") != 1:
        raise SystemExit("unsupported .repo-layout.yml version")
    return contract


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, check=True, capture_output=True, text=True
    )
    return [line for line in out.stdout.splitlines() if line]


def check(root: Path) -> list[str]:
    contract = load_contract(root)
    violations: list[str] = []

    for required in contract["required"]:
        if required.endswith("/"):
            if not (root / required.rstrip("/")).is_dir():
                violations.append(f"required directory missing: {required}")
        elif not (root / required).is_file():
            violations.append(f"required file missing: {required}")

    files = tracked_files(root)
    dirs = contract["directories"]
    root_rules = contract["root"]

    for path in files:
        parts = path.split("/")
        if len(parts) == 1:
            name = parts[0]
            if any(fnmatch.fnmatchcase(name, pat) for pat in root_rules.get("deny", [])):
                violations.append(f"denied at repository root: {path}")
            elif not any(fnmatch.fnmatchcase(name, pat) for pat in root_rules["allow"]):
                violations.append(f"not allowed at repository root: {path}")
            continue
        top = parts[0]
        if top not in dirs:
            violations.append(f"unlisted top-level directory: {top}/ (via {path})")
            continue
        name = parts[-1]
        if not any(fnmatch.fnmatchcase(name, pat) for pat in dirs[top]["allow"]):
            violations.append(f"type not allowed in {top}/: {path}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    args = parser.parse_args()
    violations = check(Path(args.root).resolve())
    for violation in violations:
        print(f"layout violation: {violation}")
    if violations:
        print(f"{len(violations)} layout violation(s); see .repo-layout.yml and CONTRIBUTING.md")
        return 1
    print("layout OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

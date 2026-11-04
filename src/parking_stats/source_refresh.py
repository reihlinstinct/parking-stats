"""Trusted public-runner orchestration. No private code execution or network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .privacy_scan import scan_lines
from .private_builder import Reader, build
from .union import UnionError

OUTPUTS = frozenset(
    {"public/data/recovered-summary.json", "public/data/source-freshness.json"}
)
FRESH_KEYS = frozenset(
    {
        "schema_version",
        "refresh_mode",
        "ledger_observed_at",
        "email_snapshot_at",
        "email_refresh_mode",
        "email_stale",
    }
)


def freshness(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != FRESH_KEYS:
        raise UnionError("freshness_schema")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise UnionError("freshness_version")
    if (
        value["refresh_mode"] != "private_checkout_pull"
        or value["email_refresh_mode"] != "historical_snapshot_not_live"
        or value["email_stale"] is not True
    ):
        raise UnionError("freshness_mode")
    for key in ("ledger_observed_at", "email_snapshot_at"):
        stamp = value[key]
        if not isinstance(stamp, str):
            raise UnionError("freshness_time")
        try:
            if datetime.fromisoformat(stamp).tzinfo is None:
                raise ValueError
        except ValueError:
            raise UnionError("freshness_time") from None


def run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    if result.returncode:
        raise UnionError("public_git")
    return result.stdout


def diff_gate(root: Path) -> None:
    changed = run_git(root, "diff", "--name-only", "-z", "HEAD").decode().split("\0")
    untracked = (
        run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
        .decode()
        .split("\0")
    )
    if not set(changed + untracked) - {""} <= OUTPUTS:
        raise UnionError("unexpected_public_diff")
    for name in OUTPUTS:
        path = root / name
        if path.exists():
            text = Reader(root).text(name)
            if scan_lines(name, text.splitlines(), set()):
                raise UnionError("public_candidate_privacy")


def artifact_gate(root: Path) -> None:
    """Exactly two bounded public-schema files, never a workspace directory."""
    reader = Reader(root)
    config = reader.obj("config/parking-source.json")
    from .union import validate_aggregate

    payload = reader.obj("public/data/recovered-summary.json")
    validate_aggregate(
        payload, streets=set(config["streets"]), categories=set(config["categories"])
    )
    freshness(reader.obj("public/data/source-freshness.json"))
    for name in OUTPUTS:
        if (root / name).stat().st_size > 512 * 1024:
            raise UnionError("artifact_size")
        if scan_lines(name, reader.text(name).splitlines(), set()):
            raise UnionError("artifact_privacy")


def semantic(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if k != "ledger_observed_at"}


def refresh(
    public: Path,
    private: Path,
    config: Path,
    identity: Path,
    observed: str,
    temporary: Path,
    baseline_root: Path | None = None,
) -> bool:
    """Generate validated candidate files; normal data PR owns atomic publication.

    Trusted config must pin cipher and plaintext hashes. No PR/private data may
    select commands, output paths, decryption flags or public label policy.
    """
    spec = Reader(config.parent).obj(config.name)
    if set(spec) != {
        "cipher_path",
        "cipher_sha256",
        "plaintext_sha256",
        "streets",
        "categories",
    }:
        raise UnionError("refresh_config")
    for key in ("cipher_sha256", "plaintext_sha256"):
        value = spec[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise UnionError("refresh_hash_config")
    for key in ("streets", "categories"):
        if not isinstance(spec[key], list) or any(
            not isinstance(v, str) for v in spec[key]
        ):
            raise UnionError("refresh_labels")
    cipher_path = spec["cipher_path"]
    if not isinstance(cipher_path, str):
        raise UnionError("refresh_cipher_path")
    # Reader checks every parent/symlink, type and bounds before loading bytes.
    # age ASCII armor is required, so the existing bounded UTF-8 reader applies.
    source = baseline_root if baseline_root is not None else private
    Reader(source)
    # Validate the path/symlinks/type/size before binary read. Immutable checkout.
    parts = Path(cipher_path).parts
    if (
        Path(cipher_path).is_absolute()
        or not parts
        or any(p in {".", ".."} for p in parts)
    ):
        raise UnionError("cipher_path")
    path = source
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise UnionError("cipher_symlink")
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise UnionError("cipher_size")
    ciphertext = path.read_bytes()
    if hashlib.sha256(ciphertext).hexdigest() != spec["cipher_sha256"]:
        raise UnionError("cipher_hash")

    diff_gate(public)
    with tempfile.TemporaryDirectory(prefix="parking-decrypted-", dir=temporary) as raw:
        plainroot = Path(raw)
        os.chmod(plainroot, 0o700)
        result = subprocess.run(
            ["age", "--decrypt", "--identity", str(identity)],
            input=ciphertext,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode or len(result.stdout) > 20 * 1024 * 1024:
            raise UnionError("decrypt_failed")
        if hashlib.sha256(result.stdout).hexdigest() != spec["plaintext_sha256"]:
            raise UnionError("plaintext_hash")
        baseline = plainroot / "reports_raw_recovered_union.json"
        baseline.write_bytes(result.stdout)
        baseline.chmod(0o600)
        payload, meta = build(
            private,
            plainroot,
            streets=set(spec["streets"]),
            categories=set(spec["categories"]),
            ledger_observed_at=observed,
        )
        freshness(meta)
        candidates = {
            "public/data/recovered-summary.json": payload,
            "public/data/source-freshness.json": meta,
        }
        serialized = {
            name: json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            for name, value in candidates.items()
        }
        for name, text in serialized.items():
            if scan_lines(name, text.splitlines(), set()):
                raise UnionError("public_candidate_privacy")
        old_payload = Reader(public).obj("public/data/recovered-summary.json")
        meta_path = public / "public/data/source-freshness.json"
        old_meta = (
            Reader(public).obj("public/data/source-freshness.json")
            if meta_path.exists()
            else None
        )
        if old_meta is not None:
            freshness(old_meta)
            if old_payload == payload and semantic(old_meta) == semantic(meta):
                return False
        # Complete all validation before any destination mutation. Files are
        # atomically replaced individually; only a later single git commit/PR
        # can publish the multi-file set, never this local candidate checkout.
        for name, text in serialized.items():
            target = public / name
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False
            ) as f:
                f.write(text)
                staged = Path(f.name)
            os.replace(staged, target)
        diff_gate(public)
        return True


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("public", "private", "config", "identity", "temporary", "baseline"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--observed", required=True)
    args = parser.parse_args()
    try:
        changed = refresh(
            args.public,
            args.private,
            args.config,
            args.identity,
            args.observed,
            args.temporary,
            args.baseline,
        )
        print("candidate_changed" if changed else "semantic_noop")
        return 0
    except (UnionError, OSError, subprocess.SubprocessError):
        print("source_refresh_failed_closed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

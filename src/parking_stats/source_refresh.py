"""Trusted public-runner orchestration. No private code execution or network."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from .privacy_scan import scan_lines
from .private_builder import Reader, build
from .union import UnionError, validate_aggregate

DIAGNOSTIC_CODES = frozenset(
    [
        "artifact_privacy",
        "artifact_size",
        "baseline_case",
        "baseline_conflict",
        "baseline_conflicts",
        "baseline_duplicate_or_key",
        "baseline_incidents",
        "case_limit",
        "case_number",
        "cipher_hash",
        "cipher_path",
        "cipher_size",
        "cipher_symlink",
        "conflict_source",
        "conflict_unknown_case",
        "decrypt_error_limit",
        "decrypt_failed",
        "decrypt_output_limit",
        "decrypt_timeout",
        "duplicate_json_key",
        "event_schema",
        "field_schema",
        "field_value",
        "freshness_mode",
        "freshness_privacy",
        "freshness_schema",
        "freshness_time",
        "freshness_version",
        "input_json",
        "input_limit",
        "input_object",
        "input_path",
        "input_read",
        "input_root",
        "input_size_or_type",
        "input_symlink",
        "input_total_size",
        "invalid_date",
        "label_limit",
        "label_policy",
        "label_privacy",
        "ledger_csv",
        "ledger_duplicate_folder",
        "ledger_empty",
        "ledger_filed_date",
        "ledger_folder",
        "ledger_headers",
        "ledger_limit",
        "ledger_overflow",
        "ledger_record_path",
        "ledger_reference",
        "ledger_reference_conflict",
        "ledger_short_row",
        "origin",
        "plaintext_hash",
        "public_basis",
        "public_candidate_privacy",
        "public_count",
        "public_date_coverage",
        "public_dates",
        "public_git",
        "public_group_count",
        "public_labels",
        "public_missing_count",
        "public_month",
        "public_months",
        "public_privacy",
        "public_schema",
        "public_sum",
        "refresh_cipher_path",
        "refresh_config",
        "refresh_hash_config",
        "refresh_labels",
        "snapshot_time",
        "snapshot_timezone",
        "source_id",
        "source_replay_conflict",
        "unexpected_public_diff",
        "unresolved_count",
        "unresolved_row",
        "unresolved_schema",
    ]
)
STAGES = frozenset(
    {
        "startup",
        "configuration",
        "cipher_read",
        "public_precheck",
        "decrypt",
        "plaintext_verify",
        "private_build",
        "candidate_validate",
        "existing_compare",
        "candidate_write",
        "public_postcheck",
    }
)
_STAGE: ContextVar[str] = ContextVar("refresh_stage", default="startup")


def diagnostic(error: BaseException, stage: str) -> str:
    """Emit only literal enumerated values, never exception prose or source values."""
    safe_stage = stage if type(stage) is str and stage in STAGES else "startup"
    code = "unexpected_error"
    if type(error) is UnionError and len(error.args) == 1:
        arg = error.args[0]
        if type(arg) is str and arg in DIAGNOSTIC_CODES:
            code = arg
    elif isinstance(error, OSError):
        code = "system_io_error"
    elif isinstance(error, subprocess.SubprocessError):
        code = "subprocess_error"
    return "source_refresh_failed_closed stage=" + safe_stage + " code=" + code


DECRYPT_OUTPUT_LIMIT = 20 * 1024 * 1024
DECRYPT_ERROR_LIMIT = 65536
DECRYPT_TIMEOUT = 60

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


def decrypt(ciphertext: bytes, identity: Path) -> bytes:
    """Bound both pipes during age execution; kill on cap, failure or deadline."""
    with tempfile.TemporaryFile() as source:
        source.write(ciphertext)
        source.seek(0)
        process = subprocess.Popen(
            ["age", "--decrypt", "--identity", str(identity)],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output = bytearray()
        errors = 0
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "out")
        selector.register(process.stderr, selectors.EVENT_READ, "err")
        deadline = time.monotonic() + DECRYPT_TIMEOUT
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UnionError("decrypt_timeout")
                for key, _ in selector.select(min(remaining, 1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data == "out":
                        if len(output) + len(chunk) > DECRYPT_OUTPUT_LIMIT:
                            raise UnionError("decrypt_output_limit")
                        output.extend(chunk)
                    else:
                        errors += len(chunk)
                        if errors > DECRYPT_ERROR_LIMIT:
                            raise UnionError("decrypt_error_limit")
            if process.wait(timeout=max(0.01, deadline - time.monotonic())):
                raise UnionError("decrypt_failed")
            return bytes(output)
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            process.stderr.close()


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
    _STAGE.set("configuration")
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
        if isinstance(value, list):
            if len(value) != 16 or any(
                not isinstance(chunk, str) or len(chunk) != 4 for chunk in value
            ):
                raise UnionError("refresh_hash_config")
            value = "".join(value)
            spec[key] = value
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
    _STAGE.set("cipher_read")
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

    _STAGE.set("public_precheck")
    diff_gate(public)
    with tempfile.TemporaryDirectory(prefix="parking-decrypted-", dir=temporary) as raw:
        plainroot = Path(raw)
        os.chmod(plainroot, 0o700)
        _STAGE.set("decrypt")
        plaintext = decrypt(ciphertext, identity)
        _STAGE.set("plaintext_verify")
        if hashlib.sha256(plaintext).hexdigest() != spec["plaintext_sha256"]:
            raise UnionError("plaintext_hash")
        baseline = plainroot / "reports_raw_recovered_union.json"
        baseline.write_bytes(plaintext)
        baseline.chmod(0o600)
        _STAGE.set("private_build")
        payload, meta = build(
            private,
            plainroot,
            streets=set(spec["streets"]),
            categories=set(spec["categories"]),
            ledger_observed_at=observed,
        )
        _STAGE.set("candidate_validate")
        validate_aggregate(
            payload, streets=set(spec["streets"]), categories=set(spec["categories"])
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
            if len(text.encode("utf-8")) > 512 * 1024:
                raise UnionError("artifact_size")
            if scan_lines(name, text.splitlines(), set()):
                raise UnionError("public_candidate_privacy")
        _STAGE.set("existing_compare")
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
        _STAGE.set("candidate_write")
        for name, text in serialized.items():
            target = public / name
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False
            ) as f:
                f.write(text)
                staged = Path(f.name)
            os.replace(staged, target)
        _STAGE.set("public_postcheck")
        diff_gate(public)
        return True


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("public", "private", "config", "identity", "temporary", "baseline"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--observed", required=True)
    args = parser.parse_args()
    _STAGE.set("startup")
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
    except Exception as error:
        print(diagnostic(error, _STAGE.get()), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

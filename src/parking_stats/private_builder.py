"""Bounded offline private checkout adapter. No decrypt, network or public writes."""

from __future__ import annotations

import csv
import io
import json
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from .privacy_scan import scan_lines
from .union import CASE, FIELDS, MAX_CASES, UnionError, aggregate

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024


class Budget:
    def __init__(self) -> None:
        self.total = 0


class Reader:
    def __init__(self, root: Path, budget: Budget | None = None):
        if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
            raise UnionError("input_root")
        self.root = root
        self.budget = budget if budget is not None else Budget()

    def text(self, relative: str) -> str:
        parts = Path(relative).parts
        if (
            not parts
            or Path(relative).is_absolute()
            or any(p in {".", ".."} for p in parts)
        ):
            raise UnionError("input_path")
        path = self.root
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise UnionError("input_symlink")
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise UnionError("input_size_or_type")
            self.budget.total += info.st_size
            if self.budget.total > MAX_TOTAL_BYTES:
                raise UnionError("input_total_size")
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise UnionError("input_read") from None

    def obj(self, relative: str) -> dict[str, Any]:
        text = self.text(relative)
        try:
            obj = json.loads(text, object_pairs_hook=_unique)
        except (json.JSONDecodeError, RecursionError):
            raise UnionError("input_json") from None
        if not isinstance(obj, dict):
            raise UnionError("input_object")
        return obj


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UnionError("duplicate_json_key")
        result[key] = value
    return result


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise UnionError("snapshot_time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise UnionError("snapshot_time") from None
    if parsed.tzinfo is None:
        raise UnionError("snapshot_timezone")
    return value


CITY_EVENTS_PATH = "ledger/stats-city-events.json"
CITY_FIELDS = frozenset({"createdon", "streetname", "subjectname"})


def _city_events(private: Reader) -> tuple[list[dict[str, Any]], str | None]:
    """Strictly validate the optional city-opening-event envelope.

    Absent file means no city events (None snapshot). Present means it must be
    exactly {schema_version:1, snapshot_at, events}; every event is a
    city_email event carrying only reviewed reducer fields. Replay and
    per-event validation then happen in union.reduce_events.
    """
    path = private.root / CITY_EVENTS_PATH
    if not path.is_symlink() and not path.exists():
        return [], None
    envelope = private.obj(CITY_EVENTS_PATH)
    if set(envelope) != {"schema_version", "snapshot_at", "events"}:
        raise UnionError("city_events_schema")
    if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise UnionError("city_events_schema")
    snapshot = _timestamp(envelope["snapshot_at"])
    events = envelope["events"]
    if not isinstance(events, list) or len(events) > MAX_CASES:
        raise UnionError("city_events_schema")
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("origin") != "city_email"
            or not isinstance(event.get("fields"), dict)
            or not set(event["fields"]) <= CITY_FIELDS
        ):
            raise UnionError("city_events_event")
        _timestamp(event.get("observed_at"))
    return events, snapshot


def build(
    private_root: Path,
    decrypted_root: Path,
    *,
    streets: set[str],
    categories: set[str],
    ledger_observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return only approved public aggregates/freshness, never private state.

    Private files are immutable untrusted data, not executable code. Caller must
    pin the checkout and verify/decrypt ciphertext before invoking this adapter.
    """
    observed = _timestamp(ledger_observed_at)
    budget = Budget()
    private = Reader(private_root, budget)
    plain = Reader(decrypted_root, budget)
    baseline = plain.obj("reports_raw_recovered_union.json")
    snapshot = _timestamp(baseline.get("snapshot_at"))
    incidents = baseline.get("incidents")
    if not isinstance(incidents, list) or len(incidents) > MAX_CASES:
        raise UnionError("baseline_incidents")
    events: list[dict[str, Any]] = []
    refs: set[str] = set()
    for row in incidents:
        if not isinstance(row, dict) or not isinstance(row.get("case_number"), str):
            raise UnionError("baseline_case")
        ref = row["case_number"]
        if not CASE.fullmatch(ref) or ref in refs:
            raise UnionError("baseline_duplicate_or_key")
        refs.add(ref)
        events.append(
            dict(
                case_number=ref,
                origin=row.get("origin"),
                source_id="baseline/" + ref,
                observed_at=snapshot,
                fields={k: row[k] for k in FIELDS if k in row},
            )
        )
    conflicts = baseline.get("conflicts", [])
    if not isinstance(conflicts, list) or len(conflicts) > MAX_CASES:
        raise UnionError("baseline_conflicts")
    for item in conflicts:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("field"), str)
            or item["field"] not in FIELDS
        ):
            raise UnionError("baseline_conflict")
        if (
            not isinstance(item.get("case_number"), str)
            or item["case_number"] not in refs
        ):
            raise UnionError("conflict_unknown_case")
        mid = item.get("message_id")
        if not isinstance(mid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", mid):
            raise UnionError("conflict_source")
        events.append(
            dict(
                case_number=item["case_number"],
                origin="city_email",
                source_id=mid + "/" + item["field"],
                observed_at=snapshot,
                fields={item["field"]: item.get("email_value")},
            )
        )
    city_events, city_snapshot = _city_events(private)
    events.extend(city_events)
    csv_text = private.text("ledger/reports.csv")
    try:
        csv_input = csv.DictReader(io.StringIO(csv_text), strict=True)
        headers = csv_input.fieldnames
    except csv.Error:
        raise UnionError("ledger_csv") from None
    required = {
        "folder_id",
        "filed_date",
        "municipal_ref",
        "street",
        "city_opened_at",
        "ref_mapping",
        "metadata_record",
        "municipal_event_number",
    }
    if (
        not headers
        or len(headers) != len(set(headers))
        or any(not h for h in headers)
        or not required <= set(headers)
    ):
        raise UnionError("ledger_headers")
    try:
        rows: list[dict[str | Any, Any]] = []
        for row in csv_input:
            if len(rows) >= MAX_CASES:
                raise UnionError("ledger_limit")
            rows.append(row)
    except csv.Error:
        raise UnionError("ledger_csv") from None
    if not rows:
        raise UnionError("ledger_empty")
    folders: set[str] = set()
    unresolved: set[str] = set()
    for row in rows:
        if any(value is None for key, value in row.items() if key is not None):
            raise UnionError("ledger_short_row")
        extra = row.pop(None, [])
        if any(extra):
            raise UnionError("ledger_overflow")
        folder = row.get("folder_id")
        if not isinstance(folder, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,120}", folder
        ):
            raise UnionError("ledger_folder")
        if folder in folders:
            raise UnionError("ledger_duplicate_folder")
        folders.add(folder)
        if row.get("ref_mapping") == "not_filed_prepared":
            continue
        filed = row.get("filed_date")
        try:
            if (
                not isinstance(filed, str)
                or datetime.strptime(filed, "%Y-%m-%d").strftime("%Y-%m-%d") != filed
            ):
                raise ValueError
        except ValueError:
            raise UnionError("ledger_filed_date") from None
        record_path = row.get("metadata_record")
        if record_path:
            if record_path != "reports/" + folder + "/report.json":
                raise UnionError("ledger_record_path")
            # Validate referenced folder record but never execute it or infer
            # additional fields from arbitrary nested metadata.
            private.obj(record_path)
        if row.get("ref_mapping") == "unverified_fifo":
            # A FIFO guess is not an identity: never derive a case number from
            # the row (its two reference columns may even disagree). The
            # independent city events carry those case numbers.
            unresolved.add(folder)
            continue
        ref = (row.get("municipal_ref") or "").strip()
        alternate = (row.get("municipal_event_number") or "").strip()
        valid = ref if CASE.fullmatch(ref) else None
        if re.fullmatch(r"[0-9a-fA-F-]{36}", alternate):
            # A UUID in the event-number column is a transport ID, not a case
            # number: ignore it; the row stays unresolved unless municipal_ref
            # is itself a valid case number.
            alternate = ""
        if alternate:
            if not CASE.fullmatch(alternate) or (valid and alternate != valid):
                raise UnionError("ledger_reference_conflict")
            valid = alternate
        if not valid:
            if ref and not re.fullmatch(r"[0-9a-fA-F-]{36}", ref):
                raise UnionError("ledger_reference")
            unresolved.add(folder)
            continue
        fields = {"streetname": row.get("street") or None}
        if row.get("city_opened_at"):
            fields["createdon"] = row["city_opened_at"]
        events.append(
            dict(
                case_number=valid,
                origin="ledger",
                source_id="current/" + folder,
                observed_at=observed,
                fields=fields,
            )
        )
    prior_unresolved = baseline.get("unresolved_filed_rows", [])
    if not isinstance(prior_unresolved, list):
        raise UnionError("unresolved_schema")
    for row in prior_unresolved:
        if not isinstance(row, dict) or not isinstance(row.get("folder_id"), str):
            raise UnionError("unresolved_row")
        if row["folder_id"] not in folders:
            unresolved.add(row["folder_id"])
    payload = aggregate(
        events, streets=streets, categories=categories, unresolved_rows=len(unresolved)
    )
    freshness = {
        "schema_version": 2,
        "refresh_mode": "private_checkout_pull",
        "ledger_observed_at": observed,
        "email_snapshot_at": snapshot,
        "email_refresh_mode": "historical_snapshot_not_live",
        "email_stale": True,
        "city_events_snapshot_at": city_snapshot,
        "live_city_status": False,
    }
    if scan_lines("source-freshness.json", json.dumps(freshness).splitlines(), set()):
        raise UnionError("freshness_privacy")
    return payload, freshness

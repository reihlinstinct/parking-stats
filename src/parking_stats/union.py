"""Offline private case reducer and allowlisted aggregate boundary.

No network, credentials, storage writes or publication. Private reducer output must
be encrypted by the caller; only aggregate() output may cross the public boundary.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from datetime import date, datetime
from typing import Any

from .privacy_scan import scan_lines

CASE = re.compile(r"[0-9]{4}-[0-9]{7}(?:/[1-9][0-9]{0,3})?", re.ASCII)
FIELDS = frozenset({"createdon", "ClosedDateIncident", "streetname", "subjectname"})
ORIGINS = frozenset({"email_export", "ledger", "city_email"})
MAX_CASES = 100_000
PUBLIC_KEYS = frozenset(
    {
        "schema_version",
        "basis",
        "case_identifiers",
        "unresolved_rows",
        "missing_opening_date",
        "through",
        "from_date",
        "by_month",
        "by_street",
        "by_category",
    }
)


class UnionError(ValueError):
    """Safe diagnostic codes only; never echo private data."""


def _day(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise UnionError("invalid_date")
    try:
        if len(value) == 10:
            return date.fromisoformat(value).isoformat()
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        raise UnionError("invalid_date") from None


def _event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or set(event) != {
        "case_number",
        "origin",
        "source_id",
        "observed_at",
        "fields",
    }:
        raise UnionError("event_schema")
    if not isinstance(event["case_number"], str) or not CASE.fullmatch(
        event["case_number"]
    ):
        raise UnionError("case_number")
    if not isinstance(event["origin"], str) or event["origin"] not in ORIGINS:
        raise UnionError("origin")
    sid = event["source_id"]
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", sid):
        raise UnionError("source_id")
    _day(event["observed_at"])
    fields = event["fields"]
    if not isinstance(fields, dict) or not set(fields) <= FIELDS:
        raise UnionError("field_schema")
    for key, value in fields.items():
        if value is None:
            continue
        if (
            not isinstance(value, str)
            or len(value) > 200
            or any(ord(c) < 32 for c in value)
        ):
            raise UnionError("field_value")
        if key in {"createdon", "ClosedDateIncident"}:
            _day(value)
    return copy.deepcopy(event)


def reduce_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Canonical, replay-safe private state. Conflicting source IDs fail closed.

    Source IDs must be stable provider IDs/revision-and-row IDs, not array offsets.
    Sources are sorted independently of delivery order. Facts are retained per
    source, never silently reconciled. Case-number identity is the only merge key.
    """
    if not isinstance(events, list) or len(events) > MAX_CASES * 10:
        raise UnionError("input_limit")
    by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in events:
        event = _event(raw)
        identity = (event["origin"], event["source_id"])
        if identity in by_source and by_source[identity] != event:
            raise UnionError("source_replay_conflict")
        by_source[identity] = event
    cases: dict[str, list[dict[str, Any]]] = {}
    for identity in sorted(by_source):
        event = by_source[identity]
        cases.setdefault(event["case_number"], []).append(event)
    if len(cases) > MAX_CASES:
        raise UnionError("case_limit")
    return {
        "schema_version": 1,
        "incidents": [
            {"case_number": ref, "sources": cases[ref]} for ref in sorted(cases)
        ],
    }


def _labels(labels: set[str]) -> None:
    if len(labels) > 5000:
        raise UnionError("label_limit")
    for label in labels:
        if (
            not isinstance(label, str)
            or not 1 <= len(label) <= 100
            or not re.fullmatch(r"[א-תA-Za-z \"'()/\\+.,-]+", label)
        ):
            raise UnionError("label_policy")
        if scan_lines("approved-label", [label], set()):
            raise UnionError("label_privacy")


def aggregate(
    events: list[dict[str, Any]],
    *,
    streets: set[str],
    categories: set[str],
    unresolved_rows: int = 0,
) -> dict[str, Any]:
    """Count one per exact case number, use only reviewed public label lists.

    Measures with conflicting values are unknown, not first/last-source wins.
    Opening months use opening dates only. No silent filing-date fallback, status
    statistic, raw ID or private build timestamp is emitted.
    """
    _labels(streets)
    _labels(categories)
    if type(unresolved_rows) is not int or not 0 <= unresolved_rows <= MAX_CASES:
        raise UnionError("unresolved_count")
    state = reduce_events(events)
    monthly: Counter[str] = Counter()
    street_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    days: list[str] = []
    missing = 0
    for incident in state["incidents"]:
        sources = incident["sources"]
        for key, allowed, counts in (
            ("streetname", streets, street_counts),
            ("subjectname", categories, category_counts),
        ):
            values = {s["fields"].get(key) for s in sources} - {None, ""}
            label = next(iter(values)) if len(values) == 1 else None
            counts[label if label in allowed else "other_or_unknown"] += 1
        dates = {
            _day(s["fields"]["createdon"])
            for s in sources
            if s["fields"].get("createdon")
        }
        if len(dates) == 1:
            day = next(iter(dates))
            days.append(day)
            monthly[day[:7]] += 1
        else:
            missing += 1
    payload: dict[str, Any] = {
        "schema_version": 1,
        "basis": "recovered_case_identifiers_not_certified",
        "case_identifiers": len(state["incidents"]),
        "unresolved_rows": unresolved_rows,
        "missing_opening_date": missing,
        "from_date": min(days) if days else None,
        "through": max(days) if days else None,
        "by_month": dict(sorted(monthly.items())),
        "by_street": dict(sorted(street_counts.items())),
        "by_category": dict(sorted(category_counts.items())),
    }
    validate_aggregate(payload, streets=streets, categories=categories)
    return payload


def validate_aggregate(
    payload: dict[str, Any], *, streets: set[str], categories: set[str]
) -> None:
    """Strict public contract; rejects extras and attacker-controlled labels."""
    _labels(streets)
    _labels(categories)
    if set(payload) != PUBLIC_KEYS or type(payload["schema_version"]) is not int:
        raise UnionError("public_schema")
    if payload["schema_version"] != 1 or payload["basis"] != (
        "recovered_case_identifiers_not_certified"
    ):
        raise UnionError("public_basis")
    for key in ("case_identifiers", "unresolved_rows", "missing_opening_date"):
        if type(payload[key]) is not int or not 0 <= payload[key] <= MAX_CASES:
            raise UnionError("public_count")
    total = payload["case_identifiers"]
    if payload["missing_opening_date"] > total:
        raise UnionError("public_missing_count")
    for key, allowed in (("by_street", streets), ("by_category", categories)):
        values = payload[key]
        if not isinstance(values, dict) or not set(values) <= allowed | {
            "other_or_unknown"
        }:
            raise UnionError("public_labels")
        _counts(values, total)
    months = payload["by_month"]
    if not isinstance(months, dict) or len(months) > 1200:
        raise UnionError("public_months")
    for month in months:
        if not isinstance(month, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
            raise UnionError("public_month")
        _day(month + "-01")
    _counts(months, total - payload["missing_opening_date"])
    first, last = payload["from_date"], payload["through"]
    if (first is None) != (last is None):
        raise UnionError("public_dates")
    if first is not None:
        if _day(first) != first or _day(last) != last or first > last:
            raise UnionError("public_dates")
        if not months or first[:7] != min(months) or last[:7] != max(months):
            raise UnionError("public_date_coverage")
    elif months:
        raise UnionError("public_date_coverage")
    if scan_lines(
        "union-summary.json",
        json.dumps(payload, ensure_ascii=False).splitlines(),
        set(),
    ):
        raise UnionError("public_privacy")


def _counts(values: dict[str, Any], expected: int) -> None:
    if any(type(v) is not int or not 1 <= v <= MAX_CASES for v in values.values()):
        raise UnionError("public_group_count")
    if sum(values.values()) != expected:
        raise UnionError("public_sum")

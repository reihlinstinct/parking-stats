"""Snapshot-month derivation for the optional monthly private encrypted log.

The month key names the snapshot month: the calendar month that just
completed, in Asia/Jerusalem. A scheduled run starting on day one derives the
previous month; a delayed start on day two before 06:00 local is the late
start of that same monthly run and derives the same key; any other start day
exits and reports rather than guessing. A manual dispatch takes an explicit
YYYY-MM key, never a future month, and an invalid key is rejected before any
work. Idempotency (an already-recorded month no-ops) is enforced by the job
that uses these helpers, keyed by the derived or dispatched month key.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

JERUSALEM = ZoneInfo("Asia/Jerusalem")


def _aware(value: datetime) -> datetime:
    """A naive datetime means UTC; never the host's local timezone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def snapshot_month(start: datetime) -> str | None:
    """YYYY-MM key for a scheduled run start, or None when the run must exit."""
    local = _aware(start).astimezone(JERUSALEM)
    if local.day == 1 or (local.day == 2 and local.hour < 6):
        previous = local.replace(day=1) - timedelta(days=1)
        return f"{previous.year:04d}-{previous.month:02d}"
    return None


def valid_dispatch_key(key: str, now: datetime) -> bool:
    """A dispatch key is exactly YYYY-MM, a real month, and not in the future."""
    if len(key) != 7 or key[4] != "-":
        return False
    try:
        year, month = int(key[:4]), int(key[5:])
    except ValueError:
        return False
    if not 1 <= month <= 12:
        return False
    local = _aware(now).astimezone(JERUSALEM)
    return (year, month) <= (local.year, local.month)

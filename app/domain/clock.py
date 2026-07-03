"""Branch-local time helpers — the single source for tz-offset arithmetic (DRY).

Columns are naive UTC (TIMESTAMP WITHOUT TIME ZONE), so these return naive values."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def naive_utc(dt: datetime) -> datetime:
    """Convert to naive UTC — DB columns are TIMESTAMP WITHOUT TIME ZONE, and asyncpg
    rejects tz-aware values for them. Channel adapters build tz-aware timestamps from
    IG/WA/MBS payloads; this normalizes them at the domain boundary."""
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


def branch_now(tz_offset_h: int) -> datetime:
    """Wall-clock time in the branch's timezone (naive)."""
    return utc_now() + timedelta(hours=tz_offset_h)


def branch_today(tz_offset_h: int) -> date:
    return branch_now(tz_offset_h).date()


def branch_day_start_utc(now_utc_naive: datetime, tz_offset_h: int) -> datetime:
    """UTC instant of the branch-local midnight that precedes `now`."""
    local = now_utc_naive + timedelta(hours=tz_offset_h)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(hours=tz_offset_h)

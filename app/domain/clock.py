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


def as_naive_utc(value: object, *, epoch_unit: str = "s") -> datetime:
    """Coerce a channel-payload timestamp to naive UTC.

    Accepts datetime | epoch seconds/microseconds | ISO string (incl. 'Z'); a
    missing/blank value falls back to epoch 0."""
    if isinstance(value, datetime):
        return naive_utc(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1_000_000 if epoch_unit == "us" else float(value)
        return naive_utc(datetime.fromtimestamp(seconds, tz=UTC))
    if isinstance(value, str) and value:
        return naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    return datetime.fromtimestamp(0, tz=UTC).replace(tzinfo=None)


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

"""as_naive_utc — one normalizer for every channel-payload timestamp shape."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.domain.clock import as_naive_utc, utc_now


def test_none_falls_back_to_epoch_zero() -> None:
    assert as_naive_utc(None) == datetime(1970, 1, 1)
    assert as_naive_utc("") == datetime(1970, 1, 1)


def test_naive_datetime_passthrough() -> None:
    dt = datetime(2026, 7, 4, 12, 30)
    assert as_naive_utc(dt) == dt


def test_aware_datetime_converted_to_naive_utc() -> None:
    dt = datetime(2026, 7, 4, 19, 30, tzinfo=timezone(timedelta(hours=7)))
    assert as_naive_utc(dt) == datetime(2026, 7, 4, 12, 30)


def test_epoch_seconds() -> None:
    assert as_naive_utc(1_750_000_000) == datetime(2025, 6, 15, 15, 6, 40)


def test_epoch_microseconds() -> None:
    assert as_naive_utc(1_750_000_000_000_000, epoch_unit="us") == datetime(
        2025, 6, 15, 15, 6, 40)


def test_iso_string() -> None:
    assert as_naive_utc("2026-07-04T12:30:00+07:00") == datetime(2026, 7, 4, 5, 30)


def test_iso_string_with_z_suffix() -> None:
    # the old whatsapp/instagram copies crashed on 'Z' (fromisoformat pre-normalization)
    assert as_naive_utc("2026-07-04T12:30:00Z") == datetime(2026, 7, 4, 12, 30)


def test_utc_now_is_naive() -> None:
    now = utc_now()
    assert now.tzinfo is None
    assert abs((datetime.now(UTC).replace(tzinfo=None) - now).total_seconds()) < 5

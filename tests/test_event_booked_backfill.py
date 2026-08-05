"""The backfill of event_booked_at must land on the same instant the live path would.

The CRM sends Jakarta offsets and every column here is TIMESTAMP WITHOUT TIME ZONE, so a
backfill that truncates the string stores local time as if it were UTC — seven hours adrift
from every row the gate writes, and only in the backfilled ones."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

from app.modules.crm.gate import parse_won_at  # noqa: E402

_PATH = (Path(__file__).resolve().parents[1] / "migrations" / "versions"
         / "20260805_1900_crmevb0001_event_booked_at.py")
_spec = importlib.util.spec_from_file_location("_crmevb0001", _PATH)
_mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mig)


def test_backfill_agrees_with_the_live_path_on_a_jakarta_timestamp() -> None:
    raw = "2026-07-30T13:45:40+07:00"  # a real booking, as the CRM sends it

    assert _mig._to_naive_utc(raw) == parse_won_at(raw).strftime("%Y-%m-%d %H:%M:%S")
    # And it really is converted, not truncated: 13:45 Jakarta is 06:45 UTC.
    assert _mig._to_naive_utc(raw) == "2026-07-30 06:45:40"


def test_a_timestamp_without_an_offset_is_read_as_utc() -> None:
    assert _mig._to_naive_utc("2026-07-30T13:45:40") == "2026-07-30 13:45:40"


def test_an_unusable_timestamp_degrades_to_no_date() -> None:
    for bad in (None, "", "not a date", 12345):
        assert _mig._to_naive_utc(bad) is None

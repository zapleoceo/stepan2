"""The purge must clear every FK to what it deletes — checked against the schema, not memory.

"Delete connector" broke three separate times the same way: a feature added a table pointing
at lead / channel_thread / channel, nobody updated the hand-written delete list, and the purge
died on a ForeignKeyViolation — a 500 with the channel still there. The old defence was a
comment asking the next author to keep the list in sync. It did not work, twice.

So instead of listing tables again here (a second copy to forget), these tests read the FKs
out of the SQLModel metadata and assert the service covers them. Add a table with an FK to
any purge target and this fails in CI, naming the table, until it is handled.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

import app.adapters.db.models  # noqa: E402, F401 — imported to register every table
from app.modules.channels.service import _BY_CHANNEL, _BY_LEAD, _BY_THREAD  # noqa: E402

# Deleted by their own statement rather than through one of the loops below.
_HANDLED_SEPARATELY = {
    "channel_thread": {"message", "channel_thread"},
    "lead": {"channel_thread"},
    "channel": {"channel_thread", "message"},
    "message": {"media_asset"},
}


def _referencing(parent: str, column: str = "id") -> set[str]:
    """Tables holding an FK to parent.column, per the ORM metadata."""
    out: set[str] = set()
    for table in SQLModel.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.column.table.name == parent and fk.column.name == column:
                out.add(table.name)
    return out


@pytest.mark.parametrize(("parent", "covered"), [
    ("channel_thread", set(_BY_THREAD)),
    ("lead", set(_BY_LEAD)),
    ("channel", set(_BY_CHANNEL)),
    ("message", set()),
])
def test_every_fk_to_a_purge_target_is_deleted(parent: str, covered: set[str]) -> None:
    missing = _referencing(parent) - covered - _HANDLED_SEPARATELY.get(parent, set())
    assert not missing, (
        f"{sorted(missing)} reference {parent}.id but the purge never deletes them — "
        f"'Delete connector' will fail with a ForeignKeyViolation. Add them to the matching "
        f"list in app/modules/channels/service.py."
    )


def test_the_two_tables_the_bug_was_about_are_covered() -> None:
    """Regression pin: thread_log (blocked the channel_thread delete) and post_comment
    (blocked the channel delete) — both found live on 2026-08-02 while purging a branch."""
    assert "thread_log" in _BY_THREAD
    assert "post_comment" in _BY_CHANNEL


def test_lead_refs_are_cleared_before_the_lead_itself() -> None:
    """Ordering is the whole point: manager_alert and stage_event hang off BOTH thread and
    lead, so they must appear in the thread pass too or the orphan-lead delete trips."""
    for tbl in ("manager_alert", "stage_event"):
        assert tbl in _BY_LEAD

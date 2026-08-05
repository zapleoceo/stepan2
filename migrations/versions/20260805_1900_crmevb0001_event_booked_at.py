"""crm_lead_state.event_booked_at: WHEN the lead signed up, not when the event is

crmev00001 stored the event's own date and nothing else, so a booking had no date to be
placed in a report window by. The tile therefore counted every booking in every period:
two clients who signed up on 30 July and 4 August both appeared under "last hour", which is
what gave the miss away.

The date was there the whole time. A booking hangs off a history row, and every history row
carries `date_time` — the same field deal_won_at reads. `_event_booked` parsed the event out
of that row and dropped the row's own timestamp, repeating exactly the mistake deal_won made
before it was given deal_won_at.

Backfilled, unlike crmev00001. The stored `raw` payload already holds the moment for rows
whose last contact result was the booking itself (`result_event`), so the number survives
this migration instead of falling to zero until each lead is next refreshed. Rows that do not
match are left null and fill on the gate's own cadence.

Revision ID: crmevb0001
Revises: crmev00001
Create Date: 2026-08-05 19:00:00
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "crmevb0001"
down_revision = "crmev00001"
branch_labels = None
depends_on = None

_TABLE = "crm_lead_state"
_COL = "event_booked_at"
_INDEX = "ix_crm_lead_state_event_booked_at"


def _to_naive_utc(value: object) -> str | None:
    """CRM timestamp → naive UTC, the same normalisation parse_won_at applies on the live
    path. The CRM sends Jakarta offsets ("2026-07-30T13:45:40+07:00") and every column here
    is TIMESTAMP WITHOUT TIME ZONE, so truncating the string instead of converting would
    store local time as if it were UTC — seven hours adrift from every row the gate writes,
    and only in the backfilled ones, which is the kind of split that is never noticed."""
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _backfill() -> None:
    """Parsed in Python, not SQL: `raw` is a text column, and a ::jsonb cast would abort the
    whole migration on one malformed payload — and would not run on SQLite at all."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, raw FROM {_TABLE} WHERE event_at IS NOT NULL AND raw IS NOT NULL")
    ).fetchall()
    for row_id, raw in rows:
        try:
            data = json.loads(raw or "")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        # Only when the last recorded result IS the booking: then its timestamp is the moment
        # the lead signed up. Any other result would date the booking by an unrelated contact.
        booked = data.get("event_booked_at")
        if not booked and data.get("last_result") == "result_event":
            booked = data.get("last_result_at")
        at = _to_naive_utc(booked)
        if not at:
            continue
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET {_COL} = :at WHERE id = :id"),
            {"at": at, "id": row_id},
        )


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COL, sa.DateTime(), nullable=True))
    op.create_index(_INDEX, _TABLE, [_COL])
    _backfill()


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COL)

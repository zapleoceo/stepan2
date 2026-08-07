"""channel.read_only: a session we hold to read, never to write

A manager's WhatsApp number is linked so we can see where a lead goes after Stepan hands
over the phone. The human on the other end keeps working that chat from their own handset,
so nothing may leave through it.

The flag already existed inside the encrypted session dump, and the send gate reads it from
there. That was enough to stop delivery and not enough to stop the WORK: the reply
dispatcher picks threads in SQL, cannot decrypt anything, and so kept spending a broker call
per manager message to write answers that were then discarded. Measured on the first two
live numbers: five generated, five skipped, nothing sent.

A column answers the question where the question is asked. Stage 6 needs the same answer for
a second reason — these contacts are not Stepan's leads and must stay out of the funnel.

Backfilled from the dumps, which are the only truth that exists today.

Revision ID: chanro0001
Revises: sndinb0001
Create Date: 2026-08-07 05:00:00
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "chanro0001"
down_revision = "sndinb0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel",
        sa.Column("read_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _backfill_from_session_dumps()


def _backfill_from_session_dumps() -> None:
    """Carry the flag over from the encrypted dumps so a paired number is not silently
    demoted to writable by this very migration.

    Decryption is best-effort: a dump we cannot read (rotated key, foreign row) leaves the
    channel at the safe default of False — which is what every non-WhatsApp channel is, and
    what the old three-field pairing form always produced."""
    try:
        from app.adapters.crypto import decrypt
    except Exception:  # noqa: BLE001 — migrations must run without the app importable
        return

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT s.channel_id, s.secret_enc FROM channel_session s"
            " JOIN channel c ON c.id = s.channel_id"
            " WHERE c.kind = 'whatsapp' AND s.status = 'active'"
        )
    ).fetchall()
    for channel_id, secret in rows:
        try:
            if not json.loads(decrypt(secret)).get("read_only"):
                continue
        except Exception:  # noqa: BLE001, S112 — unreadable dump → keep the safe default
            continue
        conn.execute(
            sa.text("UPDATE channel SET read_only = true WHERE id = :id"),
            {"id": channel_id},
        )


def downgrade() -> None:
    op.drop_column("channel", "read_only")

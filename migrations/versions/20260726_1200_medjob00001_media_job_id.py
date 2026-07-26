"""message.media_job_id — a queued transcription is remembered between attempts

The broker's speech-to-text went async: we submit a job and poll it. The client did that
inside one call — submit, poll up to the budget, give up — so a transcription slower than the
budget was abandoned and the whole thing started over on the next media tick.

Live on 2026-07-26: job ids 110920 and 110931 repeated across a dozen retries over ten
minutes, each attempt burning ~100 seconds of a worker slot waiting from scratch on a job that
was already running. The broker recognises the same audio and hands back the same job, so
nothing piled up — but nothing progressed either.

Storing the job id turns each retry into a one-second poll of the job already in flight. The
transcription gets as long as it needs (bounded by the existing 6h media window) instead of
having to finish inside one attempt.

Additive and idempotent.

Revision ID: medjob00001
Revises: fr33tail001
Create Date: 2026-07-26 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "medjob00001"
down_revision = "fr33tail001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("message")}
    if "media_job_id" not in cols:
        op.add_column("message", sa.Column("media_job_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "media_job_id")

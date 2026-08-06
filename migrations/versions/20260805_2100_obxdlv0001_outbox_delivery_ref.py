"""outbox.external_ref: what the transport called the message, so delivery can be resolved

Groundwork for the CRM sender, the first transport whose successful send does not mean
delivery. Its conversation/send queues and returns immediately — a 2xx is "accepted", and the
real outcome lands later as status 1 or 2. Such a row is written `queued` instead of `sent`,
and this column is how the later report finds it again.

Nullable and unbackfilled: every connector we have today confirms delivery synchronously and
keeps writing `sent`, so no existing row has anything to put here. Indexed because the only
query against it is the lookup by the transport's own id.

Revision ID: obxdlv0001
Revises: crmevb0001
Create Date: 2026-08-05 21:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "obxdlv0001"
down_revision = "crmevb0001"
branch_labels = None
depends_on = None

_TABLE = "outbox"
_COL = "external_ref"
_INDEX = "ix_outbox_external_ref"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COL, sa.String(), nullable=True))
    op.create_index(_INDEX, _TABLE, [_COL])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, _COL)

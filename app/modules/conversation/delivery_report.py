"""Resolve a `queued` outbox row once the transport says what became of the message.

Every connector we had answered for delivery: instagrapi and the Graph API both return after
the message exists in the conversation, so a successful send was a delivered send and the row
went straight to `sent`.

The CRM's sender is the first that does not. Its conversation/send queues and returns the
conversation immediately, and the real outcome arrives later as status 1 (success) or 2 (fail)
— by a status callback if they enable one, otherwise by polling their send-message list
(Victor, 2026-08-05). Until that report lands the row sits at `queued`: handed over, outcome
unknown. This module is where the report is applied.

Deliberately not a retry. A message the provider REFUSED is not one to push again from here —
it becomes `failed`, and the same hand-off that covers any failed send takes it from there. A
second attempt at a message that may already have reached the customer is the one outcome
worth avoiding.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Outbox
from app.domain.clock import utc_now

logger = logging.getLogger(__name__)

QUEUED = "queued"


async def resolve(
    session: AsyncSession, branch_id: int, external_ref: str, *,
    delivered: bool, error: str | None = None,
) -> Outbox | None:
    """Apply one delivery report. Returns the row it resolved, or None if there was none.

    Scoped by branch as well as by reference: the id comes from another company's system, and
    a lookup on it alone would let one tenant's report land on another tenant's row.

    Idempotent by construction — it only ever moves a row OUT of `queued`, so a report
    repeated by a retry, or one arriving after a poll already resolved the same row, finds
    nothing and changes nothing.
    """
    ref = (external_ref or "").strip()
    if not ref:
        return None
    row = (await session.execute(
        select(Outbox).where(
            Outbox.branch_id == branch_id,  # type: ignore[arg-type]
            Outbox.external_ref == ref,  # type: ignore[arg-type]
            Outbox.status == QUEUED,  # type: ignore[arg-type]
        )
    )).scalars().first()
    if row is None:
        logger.debug("delivery report for an unknown or already-resolved ref: %s", ref)
        return None

    if delivered:
        row.status = "sent"
        row.error = None
    else:
        # sent_at stays: it records when we handed the message over, which is true and is what
        # the anti-ban cap counted. Blanking it would silently give the branch its budget back
        # for a message the platform already saw.
        row.status = "failed"
        row.error = error or "delivery failed"
        logger.warning("delivery failed branch=%d outbox=%s ref=%s: %s",
                       branch_id, row.id, ref, row.error)
    session.add(row)
    await session.flush()
    return row


async def stale_queued(
    session: AsyncSession, branch_id: int, older_than_min: int,
) -> list[Outbox]:
    """Rows still `queued` past the point where a report should have arrived.

    Not resolved here on purpose. "No report" is not "not delivered", and guessing either way
    is worse than saying so: guess `sent` and a lead's silence looks like an answered lead,
    guess `failed` and a hand-off fires for a message the customer already read. Surfacing
    them is what lets somebody notice that the status feed itself is broken — the failure this
    project has now been bitten by twice, both times because nothing was watching a rate.
    """
    cutoff = utc_now() - timedelta(minutes=older_than_min)
    return list((await session.execute(
        select(Outbox).where(
            Outbox.branch_id == branch_id,  # type: ignore[arg-type]
            Outbox.status == QUEUED,  # type: ignore[arg-type]
            Outbox.sent_at < cutoff,  # type: ignore[operator]
        )
    )).scalars().all())

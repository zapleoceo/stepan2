"""Which of a branch's channels may be written to FIRST.

The follow-up harvest and the dormant harvest both pick threads by SQL and then decide, per
thread, whether a proactive message is allowed. Both used to assume the answer was yes for
every connector, because every connector was a DM connector. The website chat is not: the
visitor is anonymous the moment the response is written, so a nudge has no recipient.

The answer is the connector's own declaration (ConnectorSpec.proactive_outreach), read here
once per harvest instead of `if branch_id == the site branch` in four places — a branch id is
a deployment accident, and the next deployment's site branch would have a different one.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel
from app.connectors.registry import does_proactive_outreach


async def unreachable_channels(session: AsyncSession, branch_id: int) -> frozenset[int]:
    """Channel ids of this branch whose connector cannot write to a lead who is not writing.

    Returned as the exclusion set rather than the allow set: the harvests already hold a
    thread's channel_id, and an empty result then means "nothing is excluded", which is the
    right reading for every branch that has only DM connectors."""
    rows = (await session.execute(
        select(Channel.id, Channel.kind).where(Channel.branch_id == branch_id))).all()
    return frozenset(cid for cid, kind in rows
                     if cid is not None and not does_proactive_outreach(kind))

"""One action budget per ACCOUNT, shared by every mission running on it.

The bug this exists to remove is already live, before any new mission is added: DM sends
count against hourly_cap (150) in outbox.py, comment replies count against
comment_hourly_cap (20) in comments/service.py, and neither knows the other exists. An
account can therefore emit 170 actions in an hour with both limits reported as respected —
and Instagram counts actions per account, not per feature of ours.

Published guidance for a seasoned account is 100-150 unique comments a day and roughly 12-14
an hour; the account already hit a soft block twice on DM volume alone. Adding proactive
outreach as a third independent counter is how you arrive at a shadowban having obeyed every
rule you wrote for yourself.

So: the cap belongs to the connector, missions get a SHARE of it, and the share is spent
against one counter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.domain.clock import utc_now

logger = logging.getLogger(__name__)


_UNLIMITED = 10**9  # a stand-in for "no ceiling", so `left` stays an int callers can min() on


@dataclass(frozen=True)
class Spend:
    """What a channel has already used in the window, from every mission at once.

    cap <= 0 means the limit is OFF, following the convention every other cap in this project
    uses (see outbox._cap_reached and the settings schema). Reading it as "zero actions
    allowed" would silence a branch that had deliberately turned the limit off — every branch
    on production runs a positive cap today, but the test suite builds settings with 0 and
    caught this before it shipped."""

    used: int
    cap: int

    @property
    def unlimited(self) -> bool:
        return self.cap <= 0

    @property
    def left(self) -> int:
        if self.unlimited:
            return _UNLIMITED
        return max(0, self.cap - self.used)

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.left <= 0


def share_of(spend: Spend, budget_share: float) -> int:
    """How many actions THIS mission may take right now.

    Rounded DOWN, and never more than what is actually left: a mission entitled to 40% of a
    budget that is nearly spent gets what remains, not its share of the original. Rounding up
    would let three missions each claim one last action and put the account three over.
    """
    if spend.exhausted:
        return 0
    if spend.unlimited:
        return spend.left
    entitled = int(spend.cap * max(0.0, min(1.0, budget_share)))
    return max(0, min(entitled - _already_taken(spend, budget_share), spend.left))


def _already_taken(spend: Spend, budget_share: float) -> int:
    """This mission's share of what has been spent, assuming the spend is proportional.

    An approximation, and a deliberate one: tracking spend per mission would need a column
    and a migration, and would still be wrong the moment a human replies by hand from the
    phone — which happens daily and consumes the same account budget invisibly. Treating the
    spend as shared keeps the total honest, which is the number the platform sees."""
    return int(spend.used * max(0.0, min(1.0, budget_share)))


async def account_spend(session: Any, channel_id: int, cap: int) -> Spend:
    """Everything this ACCOUNT has done in the last hour, from every mission at once.

    Two tables because the two missions write to different ones — outbox for DM sends,
    post_comment for public replies — and counting only one of them is exactly the hole this
    closes. Sent DMs and posted comments are the same thing to Instagram: an action by this
    account.

    Deliberately NOT lowering either mission's own cap. Measured over 14 days the busiest
    hour on the live branch was 82 DM sends against a cap of 150, so the per-mission limits
    are not what binds — the missing ceiling on the TOTAL is. Adding one changes nothing on
    an ordinary day and stops the arithmetic that put the account at 170.
    """
    from sqlalchemy import text  # noqa: PLC0415

    cutoff = utc_now() - timedelta(hours=1)
    # outbox carries no channel_id — a queued line belongs to a THREAD, and the thread knows
    # its connector. Counted on sent_at, matching OutboxRepo.count_sent_since: scheduled_at is
    # when we meant to send, and a line delayed by a soft block would otherwise be charged to
    # the wrong hour.
    sent = (await session.execute(
        text("SELECT COUNT(*) FROM outbox o"
             " JOIN channel_thread ct ON ct.id = o.thread_id"
             " WHERE ct.channel_id = :ch AND o.status = 'sent' AND o.sent_at >= :since"),
        {"ch": channel_id, "since": cutoff})).scalar() or 0
    commented = (await session.execute(
        text("SELECT COUNT(*) FROM post_comment WHERE channel_id = :ch"
             " AND status IN ('replied', 'dm_sent') AND handled_at >= :since"),
        {"ch": channel_id, "since": cutoff})).scalar() or 0
    return Spend(used=int(sent) + int(commented), cap=max(0, cap))


def log_exhausted(channel_id: int, mission: str, spend: Spend) -> None:
    """A mission that stops because the account is out of budget is NORMAL, not an error —
    but it has to be visible, or a quiet account looks the same as a broken one. That
    ambiguity already cost a day: sends stopped at a daily cap and read as 'the bot is
    silent' until someone went looking (incident 2026-06-21)."""
    logger.info("mission %s paused on channel %d: account budget spent (%d/%d)",
                mission, channel_id, spend.used, spend.cap)

"""Did the person we answered in public actually come into DM?

The comment path has been running blind. `dm_sent` reads like a result and is not one: it
records that our public line CONTAINED an invitation, never that anyone accepted it. Nothing
downstream closed that loop, so the mission could have been converting nobody for months and
looked identical to one that worked.

The join is the comment author against a lead who wrote AFTER we replied. Instagram gives the
comment both a handle and a numeric id, and leads carry the same two — the numeric id is the
one that survives a rename, so it wins where both exist.

First numbers, on the whole of production (2026-08-05): 0 of 7 invited authors wrote in,
against 2 of 9 who got a plain answer with no invitation. Sixteen cases prove nothing on
their own — which is exactly why the counter has to exist before anyone tunes the wording.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.domain.clock import utc_now

logger = logging.getLogger(__name__)

# A comment answered in the morning and a DM three weeks later are not the same event. Two
# weeks is long enough for someone who read the reply, thought about it and came back, and
# short enough that an unrelated ad click months later is not counted as our win.
_ATTRIBUTION_DAYS = 14


@dataclass(frozen=True)
class Conversion:
    """How a group of public replies did at bringing people into DM."""

    replies: int
    arrived: int

    @property
    def rate(self) -> float:
        return (self.arrived / self.replies) if self.replies else 0.0


async def conversion_by_status(
    session: AsyncSession, branch_id: int, *, days: int = 90,
) -> dict[str, Conversion]:
    """Per outcome — invited ('dm_sent') vs answered ('replied') — how many came into DM.

    Both are counted because the interesting comparison is BETWEEN them: if inviting converts
    no better than simply being useful, the invitation is costing goodwill for nothing, and
    that is a decision about the reply text rather than about the code.
    """
    # SQL returns the facts; the attribution window is applied in Python. Doing the arithmetic
    # in SQL means make_interval / julianday — one dialect each — and the suite runs on SQLite
    # while production is Postgres. The project already carries two Postgres-only queries, and
    # they are precisely the two whose routes have no tests.
    since = utc_now() - timedelta(days=days)
    rows = (await session.execute(
        text("""
            SELECT pc.status,
                   pc.handled_at,
                   (SELECT MIN(m.occurred_at)
                      FROM lead l
                      JOIN channel_thread ct ON ct.lead_id = l.id
                      JOIN message m ON m.thread_id = ct.id AND m.direction = 'in'
                     WHERE l.branch_id = pc.branch_id
                       AND (
                         (pc.author_pk IS NOT NULL AND l.ig_user_id = pc.author_pk)
                         OR (pc.author_pk IS NULL AND pc.author_username IS NOT NULL
                             AND l.ig_username = pc.author_username)
                       )
                       AND m.occurred_at > pc.handled_at) AS first_dm
              FROM post_comment pc
             WHERE pc.branch_id = :branch
               AND pc.status IN ('dm_sent', 'replied')
               AND pc.handled_at IS NOT NULL
               AND pc.handled_at > :since
        """),
        {"branch": branch_id, "since": since},
    )).all()

    window = timedelta(days=_ATTRIBUTION_DAYS)
    tally: dict[str, list[int]] = {}
    for status, handled_at, first_dm in rows:
        seen = tally.setdefault(str(status), [0, 0])
        seen[0] += 1
        if first_dm is not None and _as_dt(first_dm) - _as_dt(handled_at) < window:
            seen[1] += 1
    return {k: Conversion(replies=v[0], arrived=v[1]) for k, v in tally.items()}


def _as_dt(value: object) -> datetime:
    """SQLite hands timestamps back as strings; Postgres as datetimes. Both arrive here."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def summarise(by_status: dict[str, Conversion]) -> str:
    """One line for the panel. Written so a zero is legible rather than hidden in a percent:
    '0 of 7' says something '0%' does not, namely how much evidence there is."""
    invited = by_status.get("dm_sent", Conversion(0, 0))
    answered = by_status.get("replied", Conversion(0, 0))
    return (f"позвали в личку: {invited.arrived} из {invited.replies} · "
            f"просто ответили: {answered.arrived} из {answered.replies}")

"""First-reply response rate — the one metric that catches a talking regression early.

Between 2026-07-12 and 2026-07-22 the share of first replies that were a canned campus
boilerplate rose 4% → 71% and nobody noticed for ten days, because no metric watched the
opener. 65% of this branch's leads never write a third message, so the first reply is where
the funnel is actually won or lost: measure whether the lead wrote back after it.

Read-only. Aggregation happens in Python (not date_trunc) so the same query runs on the
Postgres prod DB and the SQLite test DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

_DEFAULT_DAYS = 14

# One row per thread: when the bot first spoke, and whether the lead ever answered it.
_FIRST_REPLIES_SQL = text("""
    select f.tid as tid, f.fo as fo,
           (select count(*) from message m2
             where m2.thread_id = f.tid and m2.direction = 'in' and m2.occurred_at > f.fo)
           as replies
      from (select t.id as tid, min(m.occurred_at) as fo
              from channel_thread t
              join message m on m.thread_id = t.id and m.direction = 'out'
              join lead l on l.id = t.lead_id
             where l.branch_id = :branch_id
             group by t.id) f
     where f.fo >= :since
""")


@dataclass(frozen=True)
class FirstReplyDay:
    """One day's opener performance."""

    day: str  # ISO date
    first_replies: int
    answered: int

    @property
    def pct(self) -> float:
        """Share of openers the lead wrote back to, 0.0 when nothing went out that day."""
        return round(100.0 * self.answered / self.first_replies, 1) if self.first_replies else 0.0


async def first_reply_stats(
    session: AsyncSession, branch_id: int, days: int = _DEFAULT_DAYS,
) -> list[FirstReplyDay]:
    """Per-day opener stats for the branch, oldest first. Days with no opener are omitted."""
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=max(days, 1))
    rows = (await session.execute(
        _FIRST_REPLIES_SQL, {"branch_id": branch_id, "since": since})).all()
    buckets: dict[str, list[int]] = {}
    for _tid, first_out, replies in rows:
        day = _as_datetime(first_out).date().isoformat()
        bucket = buckets.setdefault(day, [0, 0])
        bucket[0] += 1
        if replies:
            bucket[1] += 1
    return [FirstReplyDay(day, total, answered)
            for day, (total, answered) in sorted(buckets.items())]


def _as_datetime(value: object) -> datetime:
    """SQLite hands back a str for a datetime column; Postgres hands back a datetime."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))

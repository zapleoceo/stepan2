"""DB access for comments we leave under other people's posts.

The queries that matter here are the ones that stop us: a post already handled, a person we
wrote to recently, a day's quota already spent. Everything the engine does is cheap except
the private-API calls, so the cheap checks all happen first.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Lead, OutboundComment
from app.domain.clock import utc_now


class OutboundRepo:
    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def seen(self, channel_id: int, media_id: str) -> bool:
        """Already judged — whether we commented or decided not to. A rejected post stays
        rejected: re-judging it every hour would bill the same verdict forever, and a judge
        that occasionally says yes on a retry turns a considered no into a coin flip."""
        row = (await self.session.execute(
            select(OutboundComment.id).where(
                OutboundComment.channel_id == channel_id,
                OutboundComment.media_id == media_id))).first()
        return row is not None

    async def wrote_to_recently(self, channel_id: int, author_pk: str, days: int) -> bool:
        """One comment per person per window. Appearing under three of somebody's posts in a
        week is not attentiveness, it is following them around."""
        cutoff = utc_now() - timedelta(days=days)
        row = (await self.session.execute(
            select(OutboundComment.id).where(
                OutboundComment.channel_id == channel_id,
                OutboundComment.author_pk == author_pk,
                OutboundComment.status == "sent",
                OutboundComment.handled_at >= cutoff))).first()
        return row is not None

    async def sent_today(self, channel_id: int) -> int:
        cutoff = utc_now() - timedelta(hours=24)
        row = (await self.session.execute(
            select(func.count()).select_from(OutboundComment).where(
                OutboundComment.channel_id == channel_id,
                OutboundComment.status == "sent",
                OutboundComment.handled_at >= cutoff))).scalar()
        return int(row or 0)

    async def candidates(self, channel_id: int, limit: int, *,
                         quiet_days: int) -> list[Lead]:
        """Leads worth visiting: they have a numeric IG id, they wrote to us at some point,
        and we have not commented at them lately.

        Ordered by their own last activity, newest first. Somebody who wrote last week
        remembers us; somebody from eight months ago will read a comment as an intrusion from
        an account they have forgotten, and the pool is far larger than the daily quota
        anyway — so the ordering decides who actually gets reached."""
        cutoff = utc_now() - timedelta(days=quiet_days)
        # Ordered by when the lead last wrote to US, falling back to when the row was created.
        # COALESCE rather than NULLS LAST: the ordering is the whole selection here (the pool
        # is orders of magnitude larger than a day's quota), and it has to behave identically
        # on the SQLite the suite runs and the Postgres production runs.
        sql = text("""
                SELECT l.* FROM lead l
                 WHERE l.branch_id = :branch
                   AND l.ig_user_id IS NOT NULL AND l.ig_user_id <> ''
                   AND NOT l.is_blocked
                   AND NOT EXISTS (
                        SELECT 1 FROM outbound_comment oc
                         WHERE oc.channel_id = :ch AND oc.author_pk = l.ig_user_id
                           AND oc.status = 'sent' AND oc.handled_at >= :cutoff)
                 ORDER BY COALESCE((SELECT MAX(m.occurred_at)
                                      FROM channel_thread ct
                                      JOIN message m ON m.thread_id = ct.id
                                                    AND m.direction = 'in'
                                     WHERE ct.lead_id = l.id), l.created_at) DESC
                 LIMIT :lim
            """).columns(*Lead.__table__.columns)
        rows = (await self.session.execute(
            select(Lead).from_statement(sql),
            {"branch": self.branch_id, "ch": channel_id,
             "cutoff": cutoff, "lim": limit})).scalars().all()
        return list(rows)

    def add(self, row: OutboundComment) -> OutboundComment:
        self.session.add(row)
        return row

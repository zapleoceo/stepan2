"""DB access for post comments — dedup on ingest, queues and caps on send."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import PostComment
from app.domain.clock import utc_now


class CommentRepo:
    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def exists(self, channel_id: int, external_id: str) -> bool:
        row = (await self.session.execute(
            select(PostComment.id).where(
                PostComment.channel_id == channel_id,
                PostComment.external_id == external_id))).first()
        return row is not None

    async def add(self, comment: PostComment) -> PostComment:
        self.session.add(comment)
        await self.session.flush()
        return comment

    async def latest_comment_time(self, channel_id: int) -> datetime | None:
        """The newest comment we've stored for this channel — the cheap `since` bound so an
        hourly walk skips everything already ingested."""
        row = (await self.session.execute(
            select(func.max(PostComment.occurred_at)).where(
                PostComment.channel_id == channel_id))).scalar()
        return row

    async def pending(self, channel_id: int, limit: int) -> list[PostComment]:
        rows = (await self.session.execute(
            select(PostComment).where(
                PostComment.channel_id == channel_id,
                PostComment.status == "pending").order_by(
                PostComment.occurred_at).limit(limit))).scalars().all()
        return list(rows)

    async def replied_last_hour(self, channel_id: int) -> int:
        cutoff = utc_now() - timedelta(hours=1)
        row = (await self.session.execute(
            select(func.count()).select_from(PostComment).where(
                PostComment.channel_id == channel_id,
                PostComment.status.in_(("replied", "dm_sent")),
                PostComment.handled_at >= cutoff))).scalar()
        return int(row or 0)

    async def replied_under_post(self, channel_id: int, media_id: str) -> int:
        row = (await self.session.execute(
            select(func.count()).select_from(PostComment).where(
                PostComment.channel_id == channel_id,
                PostComment.media_id == media_id,
                PostComment.status.in_(("replied", "dm_sent"))))).scalar()
        return int(row or 0)

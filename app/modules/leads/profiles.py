"""ProfileService — periodic IG profile refresh (S1 profiles.py loop).

Ingest passively fills ig_username/display_name/avatar_url; this fills the heavy
follower/following counts on a TTL, only for active-funnel leads (bot-silent/dormant
leads are skipped). A per-lead fetch failure leaves that lead untouched and never
crashes the loop — the port returns None and we move on."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Protocol

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Lead, _utcnow
from app.domain.enums import BOT_SILENT_STAGES

logger = logging.getLogger(__name__)

PROFILE_TTL = timedelta(hours=6)


class ProfileFetcher(Protocol):
    async def fetch_profile(self, ig_user_id: str) -> dict[str, Any] | None: ...


class ProfileService:
    """Refresh stale IG profile stats for one branch via a channel profile fetcher."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def stale_leads(self, limit: int) -> list[Lead]:
        """Active-funnel leads with an IG id whose profile is unsynced or older than TTL."""
        cutoff = _utcnow() - PROFILE_TTL
        q = (
            select(Lead)
            .where(
                Lead.branch_id == self.branch_id,
                Lead.ig_user_id.is_not(None),  # type: ignore[union-attr]
                Lead.stage.not_in(BOT_SILENT_STAGES),  # type: ignore[attr-defined]
                (Lead.profile_synced_at.is_(None))  # type: ignore[union-attr]
                | (Lead.profile_synced_at < cutoff),  # type: ignore[operator]
            )
            .limit(limit)
        )
        return list((await self.session.exec(q)).all())

    async def refresh(self, fetcher: ProfileFetcher, limit: int) -> int:
        """Refresh up to `limit` stale leads; returns how many were updated."""
        updated = 0
        for lead in await self.stale_leads(limit):
            assert lead.ig_user_id is not None
            profile = await fetcher.fetch_profile(lead.ig_user_id)
            if profile is None:
                continue  # transport failure — leave the lead untouched, retry next tick
            now = _utcnow()
            lead.follower_count = profile.get("follower_count")
            lead.following_count = profile.get("following_count")
            lead.last_active_at = now
            lead.profile_synced_at = now
            self.session.add(lead)
            await self.session.flush()
            updated += 1
        if updated:
            logger.info("profile refresh branch=%d: %d leads", self.branch_id, updated)
        return updated

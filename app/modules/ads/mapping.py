"""Ad→product mapping: operator-defined, with a history-derived suggestion.

The map answers "which product does this ad advertise?" so a chat gets a product the
moment the lead arrives (before Stepan qualifies). The operator owns the map; the
history suggestion only pre-fills the UI, it is never written automatically."""
from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AdProductMap, ChannelThread


class AdMappingService:
    """Read/write the branch's ad→product map and suggest one from past qualifications."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def product_for_ad(self, ad_id: str | None) -> str | None:
        if not ad_id:
            return None
        row = (await self.session.execute(
            select(AdProductMap.product_slug).where(
                AdProductMap.branch_id == self.branch_id, AdProductMap.ad_id == ad_id)
        )).first()
        return row[0] if row else None

    async def all_mappings(self) -> dict[str, str]:
        rows = (await self.session.execute(
            select(AdProductMap.ad_id, AdProductMap.product_slug).where(
                AdProductMap.branch_id == self.branch_id)
        )).all()
        return {ad_id: slug for ad_id, slug in rows}

    async def upsert(self, ad_id: str, product_slug: str, actor: str | None) -> None:
        existing = (await self.session.execute(
            select(AdProductMap).where(
                AdProductMap.branch_id == self.branch_id, AdProductMap.ad_id == ad_id)
        )).scalar_one_or_none()
        if existing is None:
            self.session.add(AdProductMap(
                branch_id=self.branch_id, ad_id=ad_id,
                product_slug=product_slug, updated_by=actor))
        else:
            existing.product_slug = product_slug
            existing.updated_by = actor
            self.session.add(existing)
        await self.session.flush()

    async def clear(self, ad_id: str) -> None:
        existing = (await self.session.execute(
            select(AdProductMap).where(
                AdProductMap.branch_id == self.branch_id, AdProductMap.ad_id == ad_id)
        )).scalar_one_or_none()
        if existing is not None:
            await self.session.delete(existing)
            await self.session.flush()

    async def suggest_from_history(self) -> dict[str, str]:
        """Per ad_id, the most common non-empty product_slug its past threads landed on.

        Only a UI hint for ads with no explicit mapping — self-reinforcing if trusted
        blindly (it reflects Stepan's own past guesses), so it never writes the map."""
        rows = (await self.session.execute(
            select(
                ChannelThread.ad_id, ChannelThread.product_slug, func.count().label("n"),
            )
            .where(
                ChannelThread.ad_id.is_not(None),
                ChannelThread.product_slug.is_not(None),
                ChannelThread.product_slug != "",
            )
            .group_by(ChannelThread.ad_id, ChannelThread.product_slug)
        )).all()
        tally: dict[str, Counter] = {}
        for ad_id, slug, n in rows:
            tally.setdefault(ad_id, Counter())[slug] += int(n or 0)
        return {ad_id: counter.most_common(1)[0][0] for ad_id, counter in tally.items()}

"""Ad→product mapping: operator-defined, with a history-derived suggestion.

The map answers "which product does this ad advertise?" so a chat gets a product the
moment the lead arrives (before Stepan qualifies). The operator owns the map; the
history suggestion only pre-fills the UI, it is never written automatically."""
from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AdProductMap, ChannelThread

logger = logging.getLogger(__name__)


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

    async def product_for_creative(self, media_pk: str | None) -> str | None:
        """Product for an ad whose creative we know but whose ad_id Instagram withheld.

        The map is keyed by the ad_context ad_id, and Instagram does not always send one —
        18 threads arrived with a creative and no ad_id, so the ad anchor was silently
        skipped and Stepan opened those conversations with no product at all (thread 5036).
        The same creative almost always arrived WITH an ad_id on other threads, and that one
        is mapped, so the creative is enough to recover the answer.

        Most common wins: one creative can be reused across ads sold as different products,
        and the majority is a better guess than an arbitrary row."""
        if not media_pk:
            return None
        row = (await self.session.execute(
            select(AdProductMap.product_slug, func.count().label("n"))
            .join(ChannelThread, ChannelThread.ad_id == AdProductMap.ad_id)  # type: ignore[arg-type]
            .where(
                AdProductMap.branch_id == self.branch_id,
                ChannelThread.ad_media_id == media_pk,  # type: ignore[arg-type]
            )
            .group_by(AdProductMap.product_slug)
            .order_by(func.count().desc())
            .limit(1)
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
        await self._backfill_threads(ad_id, product_slug)

    async def _backfill_threads(self, ad_id: str, product_slug: str) -> int:
        """Проставить продукт тредам, которые пришли с этого объявления РАНЬШЕ привязки.

        Лид приходит по рекламе, продукт которой ещё не сопоставлен, — и остаётся без продукта
        навсегда: сопоставление применялось только к новым тредам, а назад никто не смотрел.
        На 31.07.2026 так накопилось 208 тредов из 2567 рекламных (8%), и все 208 старше своей
        же привязки. Живой путь при этом исправен: тредов, созданных ПОСЛЕ привязки и без
        продукта, ноль.

        Цена пропуска не в отчётах, а в разговоре: без продукта модель не получает карточку
        курса, по которому человек пришёл, и начинает выяснять то, что реклама уже сказала.

        Трогаем ТОЛЬКО пустые. product_source различает, кто поставил продукт: выбор модели по
        ходу разговора или решение менеджера — они знают о лиде больше, чем объявление, по
        которому он кликнул, и перетирать их задним числом нельзя."""
        res = await self.session.execute(text(
            "UPDATE channel_thread SET product_slug = :slug, product_source = 'ad'"
            " WHERE ad_id = :ad AND (product_slug IS NULL OR product_slug = '')"
            "   AND lead_id IN (SELECT id FROM lead WHERE branch_id = :b)"),
            {"slug": product_slug, "ad": ad_id, "b": self.branch_id})
        n = res.rowcount or 0
        if n:
            logger.info("ad %s → %s: backfilled %d thread(s) that predate the mapping",
                        ad_id, product_slug, n)
        return n

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

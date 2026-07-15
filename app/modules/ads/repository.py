"""DB access for the ad attribution map and the insight cache. No Graph calls here."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AdCreativeMap, AdInsightDaily, ChannelThread, Lead
from app.domain.clock import utc_now


async def lead_media_pks(session: AsyncSession, branch_id: int) -> list[str]:
    """Every ad_media_id our leads actually arrived from, busiest first.

    This — not the ad account's full catalogue — defines the work: we only ever need the
    handful of ads that produced leads (~50), never all 1145."""
    rows = (await session.execute(
        select(ChannelThread.ad_media_id, func.count().label("n"))
        .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
        .where(Lead.branch_id == branch_id, ChannelThread.ad_media_id.is_not(None))
        .group_by(ChannelThread.ad_media_id)
        .order_by(func.count().desc())
    )).all()
    return [pk for pk, _ in rows]


async def mapped_media_pks(session: AsyncSession, branch_id: int) -> set[str]:
    rows = (await session.execute(
        select(AdCreativeMap.media_pk).where(AdCreativeMap.branch_id == branch_id)
    )).all()
    return {pk for (pk,) in rows}


async def unmapped_media_pks(session: AsyncSession, branch_id: int) -> list[str]:
    """Lead media that has no ad yet — the only reason to walk Graph at all."""
    known = await mapped_media_pks(session, branch_id)
    return [pk for pk in await lead_media_pks(session, branch_id) if pk not in known]


async def upsert_creative_map(
    session: AsyncSession, branch_id: int, rows: Iterable[dict],
) -> int:
    """Insert missing (branch, media_pk) rows. Existing rows are left alone: the mapping is
    immutable, so re-writing it would only churn synced_at and hide a real re-map bug."""
    known = await mapped_media_pks(session, branch_id)
    added = 0
    for row in rows:
        if row["media_pk"] in known:
            continue
        session.add(AdCreativeMap(branch_id=branch_id, synced_at=utc_now(), **row))
        known.add(row["media_pk"])
        added += 1
    if added:
        await session.flush()
    return added


async def ad_ids_for_leads(session: AsyncSession, branch_id: int) -> list[str]:
    """Ad ids reachable from our own leads — the scope of the insight sync."""
    rows = (await session.execute(
        select(AdCreativeMap.ad_id)
        .join(ChannelThread, ChannelThread.ad_media_id == AdCreativeMap.media_pk)
        .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
        .where(AdCreativeMap.branch_id == branch_id, Lead.branch_id == branch_id)
        .distinct()
    )).all()
    return [ad_id for (ad_id,) in rows]


async def replace_insights(
    session: AsyncSession, branch_id: int, since: date, rows: Iterable[AdInsightDaily],
) -> int:
    """Swap the rolling window: drop [since, ∞) for this branch, then insert fresh rows.

    Delete-then-insert (not upsert) because Meta can DROP a day's row entirely when it
    revises attribution; an upsert would leave the stale row behind forever. Days older than
    `since` are never touched — Meta has stopped revising them."""
    await session.execute(
        delete(AdInsightDaily).where(
            AdInsightDaily.branch_id == branch_id, AdInsightDaily.day >= since)
    )
    count = 0
    for row in rows:
        session.add(row)
        count += 1
    await session.flush()
    return count


async def last_synced_at(session: AsyncSession, branch_id: int) -> datetime | None:
    """Freshness stamp for the UI — never let the operator guess how old a number is."""
    return (await session.execute(
        select(func.max(AdInsightDaily.synced_at)).where(AdInsightDaily.branch_id == branch_id)
    )).scalar_one_or_none()

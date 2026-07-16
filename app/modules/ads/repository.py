"""DB access for the ad attribution map and the insight cache. No Graph calls here."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import (
    AdCreativeMap,
    AdInsightDaily,
    AdMediaMiss,
    ChannelThread,
    Lead,
)
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


# Retry a miss after 2^attempts hours, capped — a medium that looked dead because of a
# throttle deserves another try; one that truly has no ad should stop costing a walk.
_MISS_BACKOFF_CAP_H = 24


def _miss_due_at(attempts: int, last_try: datetime) -> datetime:
    return last_try + timedelta(hours=min(2 ** attempts, _MISS_BACKOFF_CAP_H))


async def media_to_skip(session: AsyncSession, branch_id: int) -> set[str]:
    """Media whose retry is not due yet — excluded so the Graph budget goes to media that can
    actually resolve, above all a newly launched ad nobody has mapped yet."""
    rows = (await session.execute(
        select(AdMediaMiss.media_pk, AdMediaMiss.attempts, AdMediaMiss.last_try_at)
        .where(AdMediaMiss.branch_id == branch_id)
    )).all()
    now = utc_now()
    return {pk for pk, attempts, last_try in rows if _miss_due_at(attempts, last_try) > now}


async def record_hunt_attempt(
    session: AsyncSession, branch_id: int, media_pks: Iterable[str],
) -> int:
    """Bump the attempt counter for media we are about to hunt.

    Stamped up-front so the backoff holds even when the hunt is throttled — the case that
    matters most, since hunting is what earns the throttle."""
    pks = list(media_pks)
    if not pks:
        return 0
    existing = {
        row.media_pk: row for row in (await session.execute(
            select(AdMediaMiss).where(
                AdMediaMiss.branch_id == branch_id, AdMediaMiss.media_pk.in_(pks))
        )).scalars().all()
    }
    now = utc_now()
    for pk in pks:
        row = existing.get(pk)
        if row is None:
            session.add(AdMediaMiss(branch_id=branch_id, media_pk=pk, attempts=1,
                                    last_try_at=now))
        else:
            row.attempts += 1
            row.last_try_at = now
            session.add(row)
    await session.flush()
    return len(pks)


async def clear_hunt_attempts(
    session: AsyncSession, branch_id: int, media_pks: Iterable[str],
) -> None:
    """A medium that resolved must not keep a stale attempt row holding it back later."""
    pks = list(media_pks)
    if pks:
        await session.execute(delete(AdMediaMiss).where(
            AdMediaMiss.branch_id == branch_id, AdMediaMiss.media_pk.in_(pks)))


async def oldest_insight_day(session: AsyncSession, branch_id: int) -> date | None:
    """How far back the spend cache reaches. None = nothing cached yet.

    The reports panel lets an operator pick any date range, but a rolling window only holds
    the recent days — so a 30-day view would show 30 days of leads against 14 days of spend
    and quietly halve the cost per lead. This is what the backfill walks backwards from."""
    return (await session.execute(
        select(func.min(AdInsightDaily.day)).where(AdInsightDaily.branch_id == branch_id)
    )).scalar_one_or_none()


async def insert_insights(
    session: AsyncSession, branch_id: int, rows: Iterable[AdInsightDaily],
) -> int:
    """Insert backfilled days. Insert-only, never delete: these days are older than Meta's
    revision horizon, so re-fetching them would only risk losing what we already hold."""
    have = {
        (ad_id, day) for ad_id, day in (await session.execute(
            select(AdInsightDaily.ad_id, AdInsightDaily.day)
            .where(AdInsightDaily.branch_id == branch_id)
        )).all()
    }
    added = 0
    for row in rows:
        if (row.ad_id, row.day) in have:
            continue
        session.add(row)
        have.add((row.ad_id, row.day))
        added += 1
    if added:
        await session.flush()
    return added

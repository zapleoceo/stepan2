"""Two things that keep the ad numbers honest over time.

1. Misses. Six lead media resolve to no ad at all. Re-hunting them every tick costs a full
   catalogue walk that ends in an account-wide throttle (measured: 11 aborted walks in 3h) —
   and that throttle is the same budget a NEWLY launched ad needs to be discovered with. So
   the dead media were degrading the very thing the sync exists for.
2. Backfill. The rolling window holds ~14 days, but the panel lets an operator pick any
   range. Thirty days of leads against fourteen days of spend halves the cost per lead into a
   number that looks perfectly plausible — the worst kind of wrong.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from test_ads_sync import CODE, PK, FakeClient, _ad, _branch_with_lead, _svc

from app.adapters.db.models import AdCreativeMap, AdInsightDaily, AdMediaMiss
from app.domain.clock import utc_now
from app.modules.ads.sync import BACKFILL_CHUNK_DAYS

TODAY = date(2026, 7, 16)


def _row(ad_id="ad1", day=TODAY, spend="1.00"):
    from app.adapters.meta_ads import InsightRow
    return InsightRow(ad_id=ad_id, day=day, spend=Decimal(spend), impressions=1, reach=1,
                      clicks=1, conv_started=1, conv_depth_2=1, conv_depth_3=1,
                      conv_depth_5=1, blocks=0)


# ─── misses ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_hunt_that_finds_nothing_records_the_attempt(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode="SOMETHING_ELSE")])
    assert await _svc(db_session, bid, client).sync_map() == 0
    miss = (await db_session.execute(AdMediaMiss.__table__.select())).mappings().one()
    assert (miss["media_pk"], miss["attempts"]) == (PK, 1)


@pytest.mark.asyncio
async def test_a_recent_miss_is_not_hunted_again(db_session) -> None:
    """The whole point: no Graph walk at all for media we just failed to find."""
    bid = await _branch_with_lead(db_session)
    db_session.add(AdMediaMiss(branch_id=bid, media_pk=PK, attempts=3,
                               last_try_at=utc_now()))
    await db_session.flush()
    client = FakeClient(ads=[_ad(shortcode=CODE)])
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert client.walked == []


@pytest.mark.asyncio
async def test_a_stale_miss_is_hunted_again(db_session) -> None:
    """Backoff, not a tombstone — a medium that only looked dead gets another chance."""
    bid = await _branch_with_lead(db_session)
    db_session.add(AdMediaMiss(branch_id=bid, media_pk=PK, attempts=1,
                               last_try_at=utc_now() - timedelta(days=3)))
    await db_session.flush()
    client = FakeClient(ads=[_ad(shortcode=CODE)])
    assert await _svc(db_session, bid, client).sync_map() == 1


@pytest.mark.asyncio
async def test_a_throttled_hunt_still_backs_off(db_session) -> None:
    """The deadlock this replaces: recording the attempt only after a COMPLETE hunt meant a
    throttled hunt recorded nothing — and hunting these media IS what earns the throttle, so
    the same futile walk ran every 20 minutes forever (live: 87s, 0 rows, empty miss table).
    The stamp says "we just tried", which is true however the hunt ended."""
    from app.adapters.meta_ads import MetaAdsError

    class _Dying(FakeClient):
        async def iter_ads(self, start_url=None):
            self.walked.append("ads")
            if True:  # noqa: SIM108 — keeps this an async GENERATOR, not a coroutine
                raise MetaAdsError("too many calls")
            yield  # pragma: no cover

    bid = await _branch_with_lead(db_session)
    assert await _svc(db_session, bid, _Dying()).sync_map() == 0
    miss = (await db_session.execute(AdMediaMiss.__table__.select())).mappings().one()
    assert (miss["media_pk"], miss["attempts"]) == (PK, 1)


@pytest.mark.asyncio
async def test_repeated_throttles_back_off_further(db_session) -> None:
    """Each futile hunt must widen the gap, or the loop merely runs slower."""
    from app.adapters.meta_ads import MetaAdsError

    class _Dying(FakeClient):
        async def iter_ads(self, start_url=None):
            self.walked.append("ads")
            if True:  # noqa: SIM108
                raise MetaAdsError("too many calls")
            yield  # pragma: no cover

    bid = await _branch_with_lead(db_session)
    await _svc(db_session, bid, _Dying()).sync_map()
    # pretend the first backoff elapsed, then fail again
    row = (await db_session.execute(
        AdMediaMiss.__table__.select())).mappings().one()
    await db_session.execute(AdMediaMiss.__table__.update().values(
        last_try_at=utc_now() - timedelta(days=3)))
    await _svc(db_session, bid, _Dying()).sync_map()
    after = (await db_session.execute(AdMediaMiss.__table__.select())).mappings().one()
    assert after["attempts"] == row["attempts"] + 1


@pytest.mark.asyncio
async def test_resolving_clears_the_miss(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    db_session.add(AdMediaMiss(branch_id=bid, media_pk=PK, attempts=1,
                               last_try_at=utc_now() - timedelta(days=3)))
    await db_session.flush()
    await _svc(db_session, bid, FakeClient(ads=[_ad(shortcode=CODE)])).sync_map()
    assert (await db_session.execute(AdMediaMiss.__table__.select())).first() is None


# ─── backfill ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_backfill_claims_the_chunk_just_older_than_what_we_have(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    db_session.add(AdInsightDaily(branch_id=bid, ad_id="ad1", day=date(2026, 7, 2),
                                  spend=Decimal("5")))
    await db_session.flush()
    older = date(2026, 7, 1)
    client = FakeClient(insights=[_row(day=older, spend="9.99")])
    assert await _svc(db_session, bid, client).backfill_insights(today=TODAY) == 1
    days = sorted(r["day"] for r in
                  (await db_session.execute(AdInsightDaily.__table__.select())).mappings())
    assert days == [older, date(2026, 7, 2)]


@pytest.mark.asyncio
async def test_backfill_never_overwrites_a_day_we_already_hold(db_session) -> None:
    """These days are past Meta's revision horizon — re-fetching could only lose data."""
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    db_session.add(AdInsightDaily(branch_id=bid, ad_id="ad1", day=date(2026, 7, 2),
                                  spend=Decimal("5")))
    await db_session.flush()
    client = FakeClient(insights=[_row(day=date(2026, 7, 2), spend="999")])
    assert await _svc(db_session, bid, client).backfill_insights(today=TODAY) == 0
    row = (await db_session.execute(AdInsightDaily.__table__.select())).mappings().one()
    assert Decimal(str(row["spend"])) == Decimal("5")


@pytest.mark.asyncio
async def test_backfill_is_a_noop_before_the_window_seeds(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(insights=[_row()])
    assert await _svc(db_session, bid, client).backfill_insights(today=TODAY) == 0
    assert client.walked == []


@pytest.mark.asyncio
async def test_backfill_stops_once_history_reaches_the_floor(db_session) -> None:
    """It must end by itself — a nightly job that never finishes is a permanent tax."""
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    db_session.add(AdInsightDaily(branch_id=bid, ad_id="ad1",
                                  day=TODAY - timedelta(days=400), spend=Decimal("1")))
    await db_session.flush()
    client = FakeClient(insights=[_row(day=TODAY - timedelta(days=500))])
    assert await _svc(db_session, bid, client).backfill_insights(today=TODAY) == 0
    assert client.walked == []


@pytest.mark.asyncio
async def test_backfill_keeps_only_ads_our_leads_came_from(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    db_session.add(AdInsightDaily(branch_id=bid, ad_id="ad1", day=date(2026, 7, 2),
                                  spend=Decimal("5")))
    await db_session.flush()
    client = FakeClient(insights=[_row(ad_id="someone_else", day=date(2026, 7, 1))])
    assert await _svc(db_session, bid, client).backfill_insights(today=TODAY) == 0


def test_chunk_is_bounded() -> None:
    """One long time_range is exactly the heavy page Graph answers 500 to."""
    assert 0 < BACKFILL_CHUNK_DAYS <= 60

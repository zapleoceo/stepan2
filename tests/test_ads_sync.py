"""AdSyncService: map lead media → Meta ad, and cache daily insights.

The client is faked, so these cover the parts that actually carry risk: that we only ever
walk Graph when there is unmapped media, that the walk stops early, that a throttle commits
nothing rather than half a month of spend, and that the rolling window replaces (not merges)
so a day Meta retracts disappears from our numbers too.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.db.models import (
    AdCreativeMap,
    AdInsightDaily,
    Branch,
    Channel,
    ChannelThread,
    Lead,
)
from app.adapters.meta_ads import AdRow, CreativeRow, InsightRow, MetaAdsRateLimited
from app.domain.enums import ChannelKind
from app.modules.ads import repository as repo
from app.modules.ads.bridge import pk_to_shortcode
from app.modules.ads.sync import AdSyncService
from app.modules.settings.service import BranchSettings

# A real verified pair (see test_ads_bridge) so the fake speaks the same ids as prod.
PK = "3931661706982573994"
CODE = "DaQEX3ds8eq"


class FakeClient:
    """Stands in for MetaAdsClient; records what was walked so we can assert we did not."""

    def __init__(
        self, *, creatives=None, ads=None, insights=None,
        raise_on: str | None = None,
    ) -> None:
        self._creatives = creatives or []
        self._ads = ads or []
        self._insights = insights or []
        self._raise_on = raise_on
        self.creatives_read = 0
        self.walked: list[str] = []

    async def iter_creatives(self, start_url=None):
        self.walked.append("creatives")
        if self._raise_on == "creatives":
            raise MetaAdsRateLimited("too many calls")
        for row in self._creatives:
            self.creatives_read += 1
            yield row

    async def iter_ads(self, start_url=None):
        self.walked.append("ads")
        for row in self._ads:
            yield row

    async def iter_insights(self, since, until):
        self.walked.append("insights")
        if self._raise_on == "insights":
            raise MetaAdsRateLimited("too many calls")
        for row in self._insights:
            yield row


def _cfg(*, token: str = "tok", account: str = "act_1") -> BranchSettings:  # noqa: S107
    """BranchSettings has many required fields; only the two ads ones matter here."""
    return BranchSettings(
        agent_enabled=True, hourly_cap=99, daily_cap=99, quiet_start=0, quiet_end=0,
        reply_delay_min_s=0, reply_delay_max_s=0, tz_offset_h=7, tg_group_id="",
        followup_enabled=False, followup_schedule_h=[], tech_search_enabled=False,
        tech_usecase_enabled=False, daily_budget_usd=0.0, crm_enabled=False,
        crm_webhook_url="", meta_pixel_id="", meta_capi_token="",
        meta_system_user_token=token, fb_account_id=account,
    )


async def _branch_with_lead(s, *, media_pk: str | None = PK) -> int:
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    channel = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(channel)
    await s.flush()
    lead = Lead(branch_id=branch.id)
    s.add(lead)
    await s.flush()
    s.add(ChannelThread(
        lead_id=lead.id, channel_id=channel.id, external_thread_id="ig-1",
        ad_media_id=media_pk, ad_id="120255671613970771",
    ))
    await s.flush()
    return branch.id


def _svc(s, branch_id: int, client) -> AdSyncService:
    svc = AdSyncService(s, branch_id, _cfg())
    svc._client = lambda: client  # noqa: SLF001 — the seam we fake
    return svc


@pytest.mark.asyncio
async def test_sync_map_resolves_media_to_ad_and_campaign(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(
        creatives=[CreativeRow(creative_id="c1", shortcode=CODE)],
        ads=[AdRow(ad_id="ad1", creative_id="c1", ad_name="Ad 2", adset_id="s1",
                   adset_name="All 18-35", campaign_id="k1",
                   campaign_name="Vibe Coding / Engagement", objective="OUTCOME_ENGAGEMENT")],
    )
    assert await _svc(db_session, bid, client).sync_map() == 1
    row = (await db_session.execute(
        AdCreativeMap.__table__.select())).mappings().one()
    assert (row["media_pk"], row["ad_id"]) == (PK, "ad1")
    assert row["campaign_name"] == "Vibe Coding / Engagement"
    assert row["shortcode"] == pk_to_shortcode(PK)


@pytest.mark.asyncio
async def test_sync_map_makes_no_graph_call_when_nothing_unmapped(db_session) -> None:
    """The steady state must be free — this is what makes a 20-min cadence affordable."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(
        creatives=[CreativeRow(creative_id="c1", shortcode=CODE)],
        ads=[AdRow(ad_id="ad1", creative_id="c1", ad_name=None, adset_id=None,
                   adset_name=None, campaign_id=None, campaign_name=None, objective=None)],
    )
    await _svc(db_session, bid, client).sync_map()
    client.walked.clear()
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert client.walked == []


@pytest.mark.asyncio
async def test_sync_map_stops_walking_creatives_once_all_found(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    extra = [CreativeRow(creative_id=f"c{i}", shortcode=f"zz{i}") for i in range(50)]
    client = FakeClient(
        creatives=[CreativeRow(creative_id="c1", shortcode=CODE), *extra],
        ads=[AdRow(ad_id="ad1", creative_id="c1", ad_name=None, adset_id=None,
                   adset_name=None, campaign_id=None, campaign_name=None, objective=None)],
    )
    await _svc(db_session, bid, client).sync_map()
    assert client.creatives_read == 1  # stopped at the hit, did not read the other 50


@pytest.mark.asyncio
async def test_sync_map_throttled_adds_nothing(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(raise_on="creatives")
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert (await repo.mapped_media_pks(db_session, bid)) == set()


@pytest.mark.asyncio
async def test_sync_map_skips_creative_that_no_ad_uses(db_session) -> None:
    """An orphan creative must leave the media unmapped, not write a row with no ad."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(creatives=[CreativeRow(creative_id="c1", shortcode=CODE)], ads=[])
    assert await _svc(db_session, bid, client).sync_map() == 0


@pytest.mark.asyncio
async def test_sync_map_tolerates_unusable_media_pk(db_session) -> None:
    bid = await _branch_with_lead(db_session, media_pk="not-a-pk")
    client = FakeClient(creatives=[CreativeRow(creative_id="c1", shortcode=CODE)])
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert client.walked == []  # bad pk filtered BEFORE any Graph call


@pytest.mark.asyncio
async def test_sync_insights_keeps_only_our_ads(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(
        branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    await db_session.flush()
    ours = InsightRow(ad_id="ad1", day=date(2026, 7, 10), spend=Decimal("12.34"),
                      impressions=100, reach=90, clicks=5, conv_started=8,
                      conv_depth_2=4, conv_depth_3=2, conv_depth_5=1, blocks=1)
    theirs = InsightRow(ad_id="other", day=date(2026, 7, 10), spend=Decimal("99"),
                        impressions=1, reach=1, clicks=1, conv_started=1,
                        conv_depth_2=1, conv_depth_3=1, conv_depth_5=1, blocks=0)
    client = FakeClient(insights=[ours, theirs])
    written = await _svc(db_session, bid, client).sync_insights(today=date(2026, 7, 15))
    assert written == 1
    row = (await db_session.execute(AdInsightDaily.__table__.select())).mappings().one()
    assert row["ad_id"] == "ad1"
    assert Decimal(str(row["spend"])) == Decimal("12.34")
    assert (row["conv_depth_3"], row["conv_depth_5"], row["blocks"]) == (2, 1, 1)


@pytest.mark.asyncio
async def test_sync_insights_throttled_leaves_previous_window_intact(db_session) -> None:
    """A partial pull would under-report spend, so a throttle must change nothing."""
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    db_session.add(AdInsightDaily(
        branch_id=bid, ad_id="ad1", day=date(2026, 7, 10), spend=Decimal("50")))
    await db_session.flush()
    client = FakeClient(raise_on="insights")
    assert await _svc(db_session, bid, client).sync_insights(today=date(2026, 7, 15)) == 0
    row = (await db_session.execute(AdInsightDaily.__table__.select())).mappings().one()
    assert Decimal(str(row["spend"])) == Decimal("50")


@pytest.mark.asyncio
async def test_sync_insights_window_replaces_so_a_retracted_day_disappears(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    db_session.add(AdInsightDaily(  # inside the window; Meta no longer reports this day
        branch_id=bid, ad_id="ad1", day=date(2026, 7, 12), spend=Decimal("7")))
    db_session.add(AdInsightDaily(  # older than the window — must survive untouched
        branch_id=bid, ad_id="ad1", day=date(2026, 6, 1), spend=Decimal("3")))
    await db_session.flush()
    client = FakeClient(insights=[])
    await _svc(db_session, bid, client).sync_insights(today=date(2026, 7, 15))
    days = [r["day"] for r in
            (await db_session.execute(AdInsightDaily.__table__.select())).mappings().all()]
    assert days == [date(2026, 6, 1)]


@pytest.mark.asyncio
async def test_no_token_means_no_work(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    svc = AdSyncService(db_session, bid, _cfg(token="", account=""))
    assert await svc.sync_map() == 0
    assert await svc.sync_insights() == 0

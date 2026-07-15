"""AdSyncService: map lead media → Meta ad, and cache daily insights.

The client is faked, so these cover what carries risk: that we walk Graph only when there is
unmapped media, that the walk stops early, that the per-ad lookup tiers are billed only when
the free permalink misses, that a missing instagram_basic caps coverage QUIETLY instead of
raising or writing a wrong ad, and that a throttle commits nothing rather than half a month
of spend.
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
from app.adapters.meta_ads import (
    AdRow,
    CreativeRow,
    InsightRow,
    MetaAdsRateLimited,
    _creative_image_hashes,
)
from app.domain.enums import ChannelKind
from app.modules.ads import repository as repo
from app.modules.ads.sync import AdSyncService
from app.modules.settings.service import BranchSettings

# A real verified pair (see test_ads_bridge) so the fake speaks the same ids as prod.
PK = "3931661706982573994"
CODE = "DaQEX3ds8eq"


def _ad(ad_id="ad1", *, shortcode=None, effective=None, source=None, campaign=None,
        hashes=()):
    return AdRow(ad_id=ad_id, creative_id=f"c-{ad_id}", ad_name=None, adset_id=None,
                 adset_name=None, campaign_id=None, campaign_name=campaign, objective=None,
                 shortcode=shortcode, effective_media_id=effective, source_media_id=source,
                 image_hashes=tuple(hashes))


class FakeClient:
    """Stands in for MetaAdsClient; records what was walked and looked up."""

    def __init__(self, *, ads=None, insights=None, media=None, creatives=None,
                 raise_on: str | None = None) -> None:
        self._creatives = creatives or []
        self._ads = ads or []
        self._insights = insights or []
        self._media = media or {}          # media_id -> shortcode (None = not permitted)
        self._raise_on = raise_on
        self.ads_read = 0
        self.media_lookups: list[str] = []
        self.walked: list[str] = []

    async def iter_ads(self, start_url=None):
        self.walked.append("ads")
        if self._raise_on == "ads":
            raise MetaAdsRateLimited("too many calls")
        for row in self._ads:
            self.ads_read += 1
            yield row

    async def iter_creatives(self, start_url=None):
        self.walked.append("creatives")
        if self._raise_on == "creatives":
            raise MetaAdsRateLimited("too many calls")
        for row in self._creatives:
            yield row

    async def media_shortcode(self, media_id):
        self.media_lookups.append(media_id)
        return self._media.get(media_id)

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
async def test_matches_on_the_ads_permalink(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode=CODE, campaign="Vibe Coding / Engagement")])
    assert await _svc(db_session, bid, client).sync_map() == 1
    row = (await db_session.execute(AdCreativeMap.__table__.select())).mappings().one()
    assert (row["media_pk"], row["ad_id"]) == (PK, "ad1")
    assert row["campaign_name"] == "Vibe Coding / Engagement"
    assert client.media_lookups == []          # permalink hit — no extra call billed


@pytest.mark.asyncio
async def test_falls_back_to_effective_media_when_permalink_misses(db_session) -> None:
    """Promoting an existing post makes dark copies, so the lead's medium is often not the one
    on the ad's permalink. The effective id resolves it on ads_management alone."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode="OTHER", effective="eff-1")], media={"eff-1": CODE})
    assert await _svc(db_session, bid, client).sync_map() == 1
    assert client.media_lookups == ["eff-1"]


@pytest.mark.asyncio
async def test_falls_back_to_source_media_last(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode="OTHER", effective="eff-1", source="src-1")],
                        media={"eff-1": None, "src-1": CODE})
    assert await _svc(db_session, bid, client).sync_map() == 1
    assert client.media_lookups == ["eff-1", "src-1"]


@pytest.mark.asyncio
async def test_without_instagram_basic_source_lookup_just_yields_nothing(db_session) -> None:
    """The System User holds no instagram_basic today, so source ids resolve to None. That
    must cap coverage QUIETLY — never raise, never write a row pointing at the wrong ad."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode="OTHER", source="src-1")], media={"src-1": None})
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert (await repo.mapped_media_pks(db_session, bid)) == set()


@pytest.mark.asyncio
async def test_lookup_tier_skipped_entirely_once_everything_matched(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad("ad-x", shortcode="OTHER", effective="eff-1"),
                             _ad("ad1", shortcode=CODE)], media={"eff-1": "NOPE"})
    await _svc(db_session, bid, client).sync_map()
    assert client.media_lookups == []          # matched on a permalink; deferred tier dropped


@pytest.mark.asyncio
async def test_makes_no_graph_call_when_nothing_unmapped(db_session) -> None:
    """The steady state must be free — this is what makes a frequent cadence affordable."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode=CODE)])
    await _svc(db_session, bid, client).sync_map()
    client.walked.clear()
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert client.walked == []


@pytest.mark.asyncio
async def test_stops_walking_ads_once_all_found(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    extra = [_ad(f"ad-{i}", shortcode=f"zz{i}") for i in range(50)]
    client = FakeClient(ads=[_ad(shortcode=CODE), *extra])
    await _svc(db_session, bid, client).sync_map()
    assert client.ads_read == 1  # stopped at the hit, did not read the other 50


@pytest.mark.asyncio
async def test_map_throttled_adds_nothing(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(raise_on="ads")
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert (await repo.mapped_media_pks(db_session, bid)) == set()


@pytest.mark.asyncio
async def test_tolerates_unusable_media_pk(db_session) -> None:
    bid = await _branch_with_lead(db_session, media_pk="not-a-pk")
    client = FakeClient(ads=[_ad(shortcode=CODE)])
    assert await _svc(db_session, bid, client).sync_map() == 0
    assert client.walked == []  # bad pk filtered BEFORE any Graph call


@pytest.mark.asyncio
async def test_sync_insights_keeps_only_our_ads(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    db_session.add(AdCreativeMap(branch_id=bid, media_pk=PK, shortcode=CODE, ad_id="ad1"))
    await db_session.flush()
    ours = InsightRow(ad_id="ad1", day=date(2026, 7, 10), spend=Decimal("12.34"),
                      impressions=100, reach=90, clicks=5, conv_started=8,
                      conv_depth_2=4, conv_depth_3=2, conv_depth_5=1, blocks=1)
    theirs = InsightRow(ad_id="other", day=date(2026, 7, 10), spend=Decimal("99"),
                        impressions=1, reach=1, clicks=1, conv_started=1,
                        conv_depth_2=1, conv_depth_3=1, conv_depth_5=1, blocks=0)
    client = FakeClient(insights=[ours, theirs])
    assert await _svc(db_session, bid, client).sync_insights(today=date(2026, 7, 15)) == 1
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


# ─── tier 3: image_hash ───────────────────────────────────────────────────────
# The same ad renders a separate IG post per placement (feed/stories/reels), and Marketing
# API exposes only one of them. All variants share ONE source image hash — that is the join.
# Measured on prod: permalink 45.2%, hash 55.4%, both 93.6%.

HASH = "955845f8c9dcd359558aec274a0c7968"   # real hash from the prod pair that proved this


@pytest.mark.asyncio
async def test_matches_via_image_hash_when_the_permalink_is_another_placement(db_session) -> None:
    """The lead's medium is an orphan creative no ad points at — only the hash links them."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(
        ads=[_ad(shortcode="OTHER_PLACEMENT", campaign="SMM / Engagement", hashes=[HASH])],
        creatives=[CreativeRow(creative_id="orphan", shortcode=CODE, image_hash=HASH)],
    )
    assert await _svc(db_session, bid, client).sync_map() == 1
    row = (await db_session.execute(AdCreativeMap.__table__.select())).mappings().one()
    assert (row["media_pk"], row["ad_id"]) == (PK, "ad1")
    assert row["campaign_name"] == "SMM / Engagement"


@pytest.mark.asyncio
async def test_hash_tier_skipped_when_permalink_already_matched(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode=CODE, hashes=[HASH])],
                        creatives=[CreativeRow("orphan", CODE, HASH)])
    await _svc(db_session, bid, client).sync_map()
    assert "creatives" not in client.walked     # tier 1 sufficed — no extra walk billed


@pytest.mark.asyncio
async def test_hash_of_a_different_ad_does_not_match(db_session) -> None:
    """A wrong hash must leave the medium unmapped, never attach it to the wrong campaign."""
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode="OTHER", hashes=["deadbeef"])],
                        creatives=[CreativeRow("orphan", CODE, HASH)])
    assert await _svc(db_session, bid, client).sync_map() == 0


@pytest.mark.asyncio
async def test_creative_without_a_hash_is_skipped(db_session) -> None:
    bid = await _branch_with_lead(db_session)
    client = FakeClient(ads=[_ad(shortcode="OTHER", hashes=[HASH])],
                        creatives=[CreativeRow("orphan", CODE, None)])
    assert await _svc(db_session, bid, client).sync_map() == 0


def test_collects_own_hash_and_every_placement_variant() -> None:
    """Real asset_feed_spec shape: placement variants live beside the creative's own image."""
    creative = {
        "image_hash": "own",
        "asset_feed_spec": {"images": [{"hash": "v1"}, {"hash": "v2"}, {"hash": "own"}]},
    }
    assert _creative_image_hashes(creative) == ("own", "v1", "v2")   # deduped, order-stable


def test_collects_nothing_when_there_is_no_image() -> None:
    assert _creative_image_hashes({}) == ()
    assert _creative_image_hashes({"asset_feed_spec": {"images": [{"x": 1}]}}) == ()

"""Sync the ad attribution map and the daily insight cache for one branch.

Design follows the two data shapes, which have opposite refresh needs:

* The map is immutable, so it is DEMAND-DRIVEN: we ask "which lead media has no ad yet?"
  and walk Graph only if the answer is non-empty — and stop the walk the moment every
  wanted shortcode is found. A steady state with no new ads costs ZERO Graph calls.
* Insights are a rolling window over only the ads our leads came from (~50, not 1145).

Rate limiting is expected, not exceptional (an ad account throttles account-wide after a
burst). A throttled sync leaves what it already committed and returns; the next tick — or a
targeted resolve when a new lead lands — carries on. Nothing here retries in a tight loop.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import AdInsightDaily
from app.adapters.meta_ads import MetaAdsClient, MetaAdsRateLimited
from app.domain.clock import utc_now
from app.modules.ads import repository as repo
from app.modules.ads.bridge import pk_to_shortcode
from app.modules.settings.service import BranchSettings

logger = logging.getLogger(__name__)

# Meta revises messaging attribution for ~7 days; 14 gives margin without re-pulling history.
INSIGHT_WINDOW_DAYS = 14


class AdSyncService:
    """Fill ad_creative_map / ad_insight_daily for a branch from its Meta ad account."""

    def __init__(
        self, session: AsyncSession, branch_id: int, cfg: BranchSettings | None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.cfg = cfg

    def _client(self) -> MetaAdsClient | None:
        if self.cfg is None:
            return None
        token, account = self.cfg.meta_system_user_token, self.cfg.fb_account_id
        if not token or not account:
            return None
        return MetaAdsClient(token, account)

    async def sync_map(self) -> int:
        """Map lead media that has no ad yet. Returns rows added.

        Skips Graph entirely when nothing is unmapped — the normal steady state."""
        client = self._client()
        if client is None:
            return 0
        wanted_pks = await repo.unmapped_media_pks(self.session, self.branch_id)
        if not wanted_pks:
            return 0
        # shortcode → media_pk, so a creative row can be recognised as one we asked for.
        wanted: dict[str, str] = {}
        for pk in wanted_pks:
            try:
                wanted[pk_to_shortcode(pk)] = pk
            except ValueError:
                logger.warning("branch=%s unusable ad_media_id %r — skipped",
                               self.branch_id, pk)
        if not wanted:
            return 0
        try:
            rows = await self._match_ads(client, wanted)
        except MetaAdsRateLimited as exc:
            logger.warning("branch=%s ad map sync throttled: %s", self.branch_id, exc)
            return 0
        return await repo.upsert_creative_map(self.session, self.branch_id, rows)

    async def _match_ads(
        self, client: MetaAdsClient, wanted: dict[str, str],
    ) -> list[dict]:
        """Walk ads, then creatives, matching the media we still need. TWO tiers, both earn
        their keep — measured on prod (1315 lead-bearing media, full walk):

          1. permalink shortcode — 45.2%. Free: it rides along in the ads walk.
          2. image_hash          — 55.4%. One creatives walk, only if tier 1 left something.
          together               — 93.6%. Complementary, not redundant: each catches what the
                                  other misses, so tier 2 runs even though tier 1 found rows.

        Why the hash works: the same ad shows in feed, stories and reels, and Meta renders a
        SEPARATE IG post per placement (adjacent shortcodes minted the same second) while
        admitting to only ONE of them via the creative's permalink. instagrapi gives us the
        post the lead actually SAW — usually a different variant. Every variant is rendered
        from one source image, so its hash is what the orphaned creative and the live ad share.

        Cost is two walks, bounded: the ads walk stops early once nothing is pending, and the
        creatives walk never starts if tier 1 already matched everything."""
        rows: list[dict] = []
        pending = dict(wanted)
        by_hash: dict[str, object] = {}

        def _take(code: str | None, ad) -> bool:
            pk = pending.pop(code, None) if code else None
            if pk is None:
                return False
            rows.append({
                "media_pk": pk, "shortcode": code, "ad_id": ad.ad_id, "ad_name": ad.ad_name,
                "adset_id": ad.adset_id, "adset_name": ad.adset_name,
                "campaign_id": ad.campaign_id, "campaign_name": ad.campaign_name,
                "objective": ad.objective,
            })
            return True

        async for ad in client.iter_ads():
            # Harvest hashes during THIS walk — tier 2 needs them, and re-walking ads would
            # double the Graph cost on an account that throttles after ~20 pages.
            for image_hash in ad.image_hashes:
                by_hash.setdefault(image_hash, ad)
            if _take(ad.shortcode, ad) and not pending:
                break

        if pending and by_hash:
            async for creative in client.iter_creatives():
                if not pending:
                    break
                if creative.shortcode not in pending or not creative.image_hash:
                    continue
                ad = by_hash.get(creative.image_hash)
                if ad is not None:
                    _take(creative.shortcode, ad)
        return rows

    async def sync_insights(self, *, today: date | None = None) -> int:
        """Refresh the rolling insight window for ads our leads came from. Returns rows."""
        client = self._client()
        if client is None:
            return 0
        if not await repo.ad_ids_for_leads(self.session, self.branch_id):
            return 0
        until = today or utc_now().date()
        since = until - timedelta(days=INSIGHT_WINDOW_DAYS)
        wanted = set(await repo.ad_ids_for_leads(self.session, self.branch_id))
        stamp = utc_now()
        rows: list[AdInsightDaily] = []
        try:
            async for row in client.iter_insights(since, until):
                if row.ad_id not in wanted:
                    continue  # account-wide edge: keep only ads we actually have leads from
                rows.append(AdInsightDaily(
                    branch_id=self.branch_id, ad_id=row.ad_id, day=row.day, spend=row.spend,
                    impressions=row.impressions, reach=row.reach, clicks=row.clicks,
                    conv_started=row.conv_started, conv_depth_2=row.conv_depth_2,
                    conv_depth_3=row.conv_depth_3, conv_depth_5=row.conv_depth_5,
                    blocks=row.blocks, synced_at=stamp,
                ))
        except MetaAdsRateLimited as exc:
            # Partial data would silently under-report spend, so commit nothing this tick.
            logger.warning("branch=%s ad insight sync throttled: %s", self.branch_id, exc)
            return 0
        return await repo.replace_insights(self.session, self.branch_id, since, rows)

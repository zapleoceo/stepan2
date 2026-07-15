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
from app.modules.ads.matcher import AdMatcher
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
        rows = await AdMatcher(client, wanted, self.branch_id).run()
        return await repo.upsert_creative_map(self.session, self.branch_id, rows)

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

"""Match a lead's IG medium to the Meta ad that ran it.

The problem this solves: one ad shows in feed, stories and reels, and Meta renders a
SEPARATE IG post per placement — adjacent shortcodes minted the same second — while admitting
to only ONE of them through the creative's permalink. instagrapi hands us the post the lead
ACTUALLY saw, which is usually a different variant. So matching on the permalink alone finds
under half of them.

Two tiers, both measured on prod (1315 lead-bearing media, full walk):

    permalink   45.2%   free — it rides along in the ads walk
    image_hash  55.4%   one creatives walk, only when tier 1 left something pending
    together    93.6%   complementary, not redundant: each finds what the other misses

Why the hash works: every placement variant is rendered from ONE source image, so its hash is
what the orphaned creative (the lead's medium) and the live ad have in common.

Both walks are best-effort. Graph 5xx and account-wide throttles are routine, so a walk that
dies keeps what it already matched — a partial map beats none, and the rest is retried on the
next tick. Losing the run to one bad page is exactly the bug this module was extracted after.
"""
from __future__ import annotations

import logging

from app.adapters.meta_ads import AdRow, MetaAdsClient, MetaAdsError

logger = logging.getLogger(__name__)


class AdMatcher:
    """Resolves {shortcode: media_pk} to ad_creative_map rows, cheapest tier first."""

    def __init__(self, client: MetaAdsClient, wanted: dict[str, str], branch_id: int) -> None:
        self._client = client
        self._branch_id = branch_id
        self._pending = dict(wanted)
        self._by_hash: dict[str, AdRow] = {}
        self._rows: list[dict] = []
        self._cut = False

    async def run(self) -> tuple[list[dict], set[str]]:
        """Returns (rows, media_pks that stayed unresolved after a COMPLETE hunt).

        The misses are only meaningful if both walks actually finished — a walk cut short by
        a throttle proves nothing about the media it never reached, so `cut_short` suppresses
        them rather than blaming media for our own rate limit."""
        await self._by_permalink()
        if self._pending and self._by_hash:
            await self._by_image_hash()
        missed = set(self._pending.values()) if not self._cut else set()
        return self._rows, missed

    def _take(self, shortcode: str | None, ad: AdRow) -> bool:
        media_pk = self._pending.pop(shortcode, None) if shortcode else None
        if media_pk is None:
            return False
        self._rows.append({
            "media_pk": media_pk, "shortcode": shortcode, "ad_id": ad.ad_id,
            "ad_name": ad.ad_name, "adset_id": ad.adset_id, "adset_name": ad.adset_name,
            "campaign_id": ad.campaign_id, "campaign_name": ad.campaign_name,
            "objective": ad.objective,
        })
        return True

    async def _by_permalink(self) -> None:
        """Tier 1. Also harvests the hashes tier 2 needs — re-walking ads to get them would
        double the Graph cost on an account that throttles after ~20 pages."""
        try:
            async for ad in self._client.iter_ads():
                for image_hash in ad.image_hashes:
                    self._by_hash.setdefault(image_hash, ad)
                if self._take(ad.shortcode, ad) and not self._pending:
                    return
        except MetaAdsError as exc:
            self._cut_short("ads", exc)

    async def _by_image_hash(self) -> None:
        """Tier 2. The lead's medium is an orphan creative no ad points at; only the source
        image it was rendered from ties it back."""
        try:
            async for creative in self._client.iter_creatives():
                if not self._pending:
                    return
                if creative.shortcode not in self._pending or not creative.image_hash:
                    continue
                ad = self._by_hash.get(creative.image_hash)
                if ad is not None:
                    self._take(creative.shortcode, ad)
        except MetaAdsError as exc:
            self._cut_short("creatives", exc)

    def _cut_short(self, walk: str, exc: MetaAdsError) -> None:
        self._cut = True
        logger.warning("branch=%s %s walk cut short (%s) — keeping %d matched, %d still pending",
                       self._branch_id, walk, exc, len(self._rows), len(self._pending))

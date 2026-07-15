"""Marketing API reader — ad creatives, ads and daily insights for one ad account.

Read-only. Two edges matter, and they must be walked SEPARATELY rather than as one nested
query: `ads?fields=creative{instagram_permalink_url}` silently returns the permalink for only
part of the ads (measured: 946 of 1145), while the standalone `adcreatives` edge exposes it
for far more (3414 of 4499). So we walk adcreatives for shortcode→creative_id, walk ads for
creative_id→ad, and join locally.

Rate limits are not hypothetical here: an ad account returns code 80004 ("too many calls to
this ad-account") after a burst of paging, and it applies to the WHOLE account for a cooldown.
Every walk therefore raises MetaAdsRateLimited with the cursor it reached, so a caller can
persist progress and resume instead of restarting 45 pages from scratch.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_CODES = {4, 17, 80000, 80003, 80004}
_PAGE_SIZE = 100


class MetaAdsError(RuntimeError):
    """Graph refused the request for a reason that will not fix itself on retry."""


class MetaAdsRateLimited(MetaAdsError):
    """Account-level throttle. `next_url` is where a resumed walk should pick up."""

    def __init__(self, message: str, next_url: str | None = None) -> None:
        super().__init__(message)
        self.next_url = next_url


@dataclass(frozen=True)
class CreativeRow:
    creative_id: str
    shortcode: str
    # The source image every placement variant was rendered from — the join key that finally
    # links an orphan creative (the medium a lead saw) back to the ad that ran it.
    image_hash: str | None = None


@dataclass(frozen=True)
class AdRow:
    ad_id: str
    creative_id: str
    ad_name: str | None
    adset_id: str | None
    adset_name: str | None
    campaign_id: str | None
    campaign_name: str | None
    objective: str | None
    # The ad's own IG post, parsed from instagram_permalink_url — present on ~1004/1145 ads.
    shortcode: str | None = None
    # EVERY source image this ad can render, one per placement variant in asset_feed_spec.
    # Placement customisation ("same ad in feed, stories and reels") renders a separate IG
    # post per placement, and Marketing API admits to only ONE of them via the permalink —
    # the lead usually saw another. The hashes are what all the variants have in common.
    # Measured on prod: permalink alone 45.2%, hash alone 55.4%, both together 93.6%.
    #
    # NOT sourced from creative.image_hash: Graph silently omits that field when creative{}
    # is nested under the ads edge (verified: 0 of 5 ads carry it, while asset_feed_spec
    # comes through fine). Only the standalone adcreatives edge returns it — which is exactly
    # where iter_creatives reads it from.
    image_hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class InsightRow:
    ad_id: str
    day: date
    spend: Decimal
    impressions: int
    reach: int
    clicks: int
    conv_started: int
    conv_depth_2: int
    conv_depth_3: int
    conv_depth_5: int
    blocks: int


# Meta's messaging-quality ladder. These are the counterpart to our own lead stages: the
# headline "conversation started" is what campaigns optimise for and is a vanity number —
# depth_3/depth_5 are what a real conversation looks like.
_ACTION_MAP = {
    "onsite_conversion.messaging_conversation_started_7d": "conv_started",
    "onsite_conversion.messaging_user_depth_2_message_send": "conv_depth_2",
    "onsite_conversion.messaging_user_depth_3_message_send": "conv_depth_3",
    "onsite_conversion.messaging_user_depth_5_message_send": "conv_depth_5",
    "onsite_conversion.messaging_block": "blocks",
}


def parse_insight(row: dict[str, Any]) -> InsightRow:
    """One Graph insights row → InsightRow. Pure, so the action-name mapping is testable."""
    actions = {a.get("action_type"): a.get("value") for a in row.get("actions") or []}
    counts = {
        field: int(actions.get(action_type) or 0)
        for action_type, field in _ACTION_MAP.items()
    }
    return InsightRow(
        ad_id=str(row["ad_id"]),
        day=date.fromisoformat(row["date_start"]),
        spend=Decimal(str(row.get("spend") or "0")),
        impressions=int(row.get("impressions") or 0),
        reach=int(row.get("reach") or 0),
        clicks=int(row.get("clicks") or 0),
        **counts,
    )


def _creative_image_hashes(creative: dict[str, Any]) -> tuple[str, ...]:
    """Every source-image hash an ad creative can render, deduped and order-stable.

    Reads creative.image_hash too, for callers that fetch a creative directly — but nested
    under the ads edge Graph omits it, so in practice the asset_feed_spec variants are what
    populate this. Pure, so the shape is testable without Graph."""
    hashes: list[str] = []
    own = creative.get("image_hash")
    if own:
        hashes.append(str(own))
    for image in (creative.get("asset_feed_spec") or {}).get("images") or []:
        value = image.get("hash")
        if value and str(value) not in hashes:
            hashes.append(str(value))
    return tuple(hashes)


class MetaAdsClient:
    """Paged, rate-limit-aware reader for one ad account."""

    def __init__(self, token: str, account_id: str, *, timeout: float = 60.0) -> None:
        if not token or not account_id:
            raise ValueError("meta ads client needs both a token and an account id")
        self._token = token
        self._account = account_id if account_id.startswith("act_") else f"act_{account_id}"
        self._timeout = timeout

    @property
    def _base(self) -> str:
        return f"https://graph.facebook.com/{settings().meta_graph_version}"

    async def _walk(
        self, edge: str, fields: str, extra: dict[str, str] | None = None,
        start_url: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every row of an edge, following paging.next to exhaustion."""
        url = start_url or f"{self._base}/{self._account}/{edge}"
        params: dict[str, str] | None = {
            "fields": fields, "limit": str(_PAGE_SIZE), **(extra or {})}
        if start_url:  # a resumed cursor URL already carries its query string
            params = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while url:
                payload = await self._get(client, url, params)
                for row in payload.get("data", []):
                    yield row
                url = (payload.get("paging") or {}).get("next") or ""
                params = None  # paging.next is fully-formed

    async def _get(
        self, client: httpx.AsyncClient, url: str, params: dict[str, str] | None,
    ) -> dict[str, Any]:
        response = await client.get(
            url, params=params, headers={"Authorization": f"Bearer {self._token}"})
        if response.status_code == 400:
            error = (response.json().get("error") or {})
            if error.get("code") in _RATE_LIMIT_CODES:
                raise MetaAdsRateLimited(str(error.get("message")), next_url=url)
            raise MetaAdsError(str(error.get("message")))
        response.raise_for_status()
        return response.json()

    async def iter_creatives(self, start_url: str | None = None) -> AsyncIterator[CreativeRow]:
        """Creatives that carry an IG permalink; the rest cannot be joined and are skipped."""
        from app.modules.ads.bridge import shortcode_from_permalink

        async for row in self._walk(
            "adcreatives", "id,instagram_permalink_url,image_hash", start_url=start_url,
        ):
            code = shortcode_from_permalink(row.get("instagram_permalink_url"))
            if code:
                yield CreativeRow(creative_id=str(row["id"]), shortcode=code,
                                  image_hash=row.get("image_hash"))

    async def iter_ads(self, start_url: str | None = None) -> AsyncIterator[AdRow]:
        """Walk ads WITH their creative's IG pointers.

        This edge — not adcreatives — is the one to match on. adcreatives finds a creative for
        96.8% of our media but most are orphans no ad references (4499 creatives vs 1154 ads),
        which stalled coverage at 38%; matching straight off the ad reaches 45.3% and, unlike
        adcreatives, every hit has an ad and therefore spend."""
        from app.modules.ads.bridge import shortcode_from_permalink  # noqa: PLC0415

        async for row in self._walk(
            "ads",
            "id,name,adset{id,name},campaign{id,name,objective},"
            "creative{id,instagram_permalink_url,asset_feed_spec}",
            start_url=start_url,
        ):
            creative = row.get("creative") or {}
            creative_id = creative.get("id")
            if not creative_id:
                continue
            adset = row.get("adset") or {}
            campaign = row.get("campaign") or {}
            yield AdRow(
                ad_id=str(row["id"]),
                creative_id=str(creative_id),
                ad_name=row.get("name"),
                adset_id=adset.get("id"),
                adset_name=adset.get("name"),
                campaign_id=campaign.get("id"),
                campaign_name=campaign.get("name"),
                objective=campaign.get("objective"),
                shortcode=shortcode_from_permalink(creative.get("instagram_permalink_url")),
                image_hashes=_creative_image_hashes(creative),
            )

    async def iter_insights(self, since: date, until: date) -> AsyncIterator[InsightRow]:
        """Daily per-ad insights over [since, until]. time_increment=1 → one row per day."""
        async for row in self._walk(
            "insights",
            "ad_id,spend,impressions,reach,clicks,actions",
            extra={
                "level": "ad",
                "time_increment": "1",
                "time_range": f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}',
            },
        ):
            if row.get("ad_id"):
                yield parse_insight(row)

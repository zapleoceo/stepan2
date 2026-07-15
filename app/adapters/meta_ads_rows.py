"""Row types and pure parsers for the Marketing API reader.

Split from the client so the shapes Graph returns can be tested without a network: the
fiddly parts here are all shape-handling (which field Graph silently omits, where the
placement variants hide), not transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


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


def edge_of(url: str) -> str:
    """Edge name from a Graph URL, for an error message that says WHICH walk broke."""
    return url.rstrip("/").split("?")[0].rsplit("/", 1)[-1]


def creative_image_hashes(creative: dict[str, Any]) -> tuple[str, ...]:
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



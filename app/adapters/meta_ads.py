"""Marketing API reader — paged, rate-limit-aware HTTP for one ad account.

Two edges matter and must be walked SEPARATELY rather than as one nested query: Graph
silently omits `image_hash` when creative{} is nested under the ads edge (verified: 0 of 5),
while the standalone adcreatives edge returns it. Row shapes and parsing live in
meta_ads_rows; this module is transport only.

Rate limits and 5xx are both routine here, not exceptional:
* code 80004 ("too many calls to this ad-account") throttles the WHOLE account for a cooldown
* asset_feed_spec makes an ads page heavy enough that Graph answers 500 partway through
  pagination at 100/page — hence a smaller page size for that walk

Both surface as MetaAdsError so a caller can keep whatever it already matched instead of
losing the run to a raw httpx error.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx

from app.adapters.meta_ads_rows import (
    AdRow,
    CreativeRow,
    InsightRow,
    MetaAdsError,
    MetaAdsRateLimited,
    creative_image_hashes,
    edge_of,
    parse_insight,
)
from app.config import settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_CODES = {4, 17, 80000, 80003, 80004}
_PAGE_SIZE = 100
# asset_feed_spec carries every placement variant, so an ads page is an order of magnitude
# heavier than a creatives page — Graph answers 500 partway through pagination at 100/page
# (live: the walk died on a later page and the whole run was lost). 25 is what survives.
_ADS_PAGE_SIZE = 25

__all__ = [
    "AdRow", "CreativeRow", "InsightRow", "MetaAdsClient", "MetaAdsError",
    "MetaAdsRateLimited", "creative_image_hashes", "parse_insight",
]


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
        start_url: str | None = None, page_size: int = _PAGE_SIZE,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every row of an edge, following paging.next to exhaustion."""
        url = start_url or f"{self._base}/{self._account}/{edge}"
        params: dict[str, str] | None = {
            "fields": fields, "limit": str(page_size), **(extra or {})}
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
        """One page. 5xx is retried, then surfaces as MetaAdsError — never as a raw httpx
        error: an uncaught HTTPStatusError killed the whole walk in prod and discarded every
        row already matched, silently (49s, 0 rows, the reason only visible in a traceback)."""
        for attempt in range(3):
            response = await client.get(
                url, params=params, headers={"Authorization": f"Bearer {self._token}"})
            if response.status_code == 400:
                error = (response.json().get("error") or {})
                if error.get("code") in _RATE_LIMIT_CODES:
                    raise MetaAdsRateLimited(str(error.get("message")), next_url=url)
                raise MetaAdsError(str(error.get("message")))
            if response.status_code >= 500:
                if attempt == 2:
                    raise MetaAdsError(f"graph {response.status_code} on {edge_of(url)}")
                await asyncio.sleep(2 * (attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        raise MetaAdsError("unreachable")  # pragma: no cover

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
            start_url=start_url, page_size=_ADS_PAGE_SIZE,
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
                image_hashes=creative_image_hashes(creative),
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

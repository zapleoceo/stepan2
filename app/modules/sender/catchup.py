"""Fetch the inbound messages their callback never managed to deliver.

Their side does not retry a failed callback — their first spec is explicit: `UrlCallback`
posts with a 5s connect timeout and "помилки лише логуються". So a message that arrives while
we are restarting is simply gone: they have it, we never did, and nothing on either side would
ever say so. Victor's answer of 2026-08-05 gives the repair — a list endpoint for a period,
reconciled by `external_id`, which is the same key the callback deduplicates on.

Two things this deliberately does NOT do.

It does not trust the window boundary. Every sweep re-covers ground it has already seen,
because a message landing on the edge of a window, or a clock that disagrees by a few seconds,
is exactly the message worth not losing. Re-fetching is free: the unique index absorbs the
repeats, and the only cost is rows we discard.

It does not fail loudly. A sweep is a safety net; if their list endpoint is down, the callback
is still the primary path and the next sweep will cover the same period again. What it DOES do
is say plainly how many messages it had to rescue — a callback quietly dropping traffic is the
failure this project keeps meeting, and it is invisible unless someone counts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.sender.inbound import CATCHUP, store, to_row

logger = logging.getLogger(__name__)

_PATH = "/api/v1/send-message"
_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class SenderApi:
    """Where their list lives and how to prove we may read it.

    `tz_offset_h` is the one field we are guessing at: their examples show
    `date_start=2026-08-05 10:00:00` with no zone, and nobody has said whose clock that is
    (open question 8). Wrong by seven hours it would quietly fetch a period nobody wrote in
    and report nothing missing, so it is a setting rather than an assumption buried in code.
    """

    base_url: str
    token: str
    project_id: str
    branch_id: str
    tz_offset_h: float = 0.0
    channel: str = "whats-app"

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip() and self.token.strip()
                    and self.project_id.strip() and self.branch_id.strip())


def window_params(api: SenderApi, since: datetime, until: datetime) -> dict[str, str]:
    """The query for one period, in THEIR clock.

    Naive UTC in, their local time out. Kept pure so the conversion can be tested without a
    network, because an off-by-a-timezone here fetches a period nobody wrote in and looks
    exactly like "nothing was missed"."""
    shift = timedelta(hours=api.tz_offset_h)
    fmt = "%Y-%m-%d %H:%M:%S"
    return {
        "type_send": "in",
        "channel": api.channel,
        "date_start": (since + shift).strftime(fmt),
        "date_end": (until + shift).strftime(fmt),
        "project_id": api.project_id,
        "branch_id": api.branch_id,
    }


def rows_of(payload: object) -> list[dict]:
    """The message list out of whatever envelope it arrives in.

    Their other endpoints answer `{"data": [...]}`; a bare list is accepted too. Anything
    else is no messages rather than an exception — a sweep that crashes on an error page
    would take the safety net down with it."""
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


async def fetch(api: SenderApi, since: datetime, until: datetime) -> list[dict]:
    """Their inbound for the period, or [] if the call fails."""
    import httpx  # noqa: PLC0415 — lazy, so unit tests need no HTTP stack

    url = api.base_url.rstrip("/") + _PATH
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(
                url,
                params=window_params(api, since, until),
                headers={"Authorization": f"Bearer {api.token}"},
            )
            resp.raise_for_status()
            return rows_of(resp.json())
    except Exception as exc:  # noqa: BLE001 — a safety net must not become a new failure
        logger.warning("sender catch-up fetch failed: %s",
                       str(exc)[:200] or type(exc).__name__)
        return []


async def sweep(
    session: AsyncSession, api: SenderApi, *, now: datetime, lookback_min: int = 30,
) -> int:
    """Pull the recent period and store whatever the callback never brought. Returns the
    number RESCUED — messages we would otherwise never have seen.

    The lookback deliberately exceeds any single outage we expect (a deploy is ~2 minutes),
    and every sweep re-covers the previous one's ground. Re-reading costs a discarded row;
    a boundary missed costs a lead nobody answers.
    """
    if not api.configured:
        return 0
    rescued = 0
    for payload in await fetch(api, now - timedelta(minutes=lookback_min), now):
        row = to_row(payload, arrived_via=CATCHUP)
        if row is None:
            continue
        if await store(session, row):
            rescued += 1
    if rescued:
        # WARNING, not INFO: every one of these is a lead whose message their callback failed
        # to deliver and nobody would otherwise have known about.
        logger.warning("sender catch-up rescued %d message(s) the callback never delivered",
                       rescued)
    return rescued

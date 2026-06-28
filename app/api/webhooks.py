"""Inbound channel webhooks. Handlers stay thin: verify/parse → return fast.
Actual ingest is enqueued downstream; here we only validate shape and ack."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_token_for(branch_id: int) -> str | None:
    """Per-branch Meta verify token from env (STEPAN2_META_VERIFY_TOKEN_<branch_id>).

    Read straight from the environment because tokens are per-branch and dynamic,
    so they don't belong on the fixed Settings model.
    """
    return os.environ.get(f"STEPAN2_META_VERIFY_TOKEN_{branch_id}")


def _count_entries(payload: dict[str, Any]) -> int:
    """Number of top-level Graph `entry` items — what we ack as `accepted`."""
    entry = payload.get("entry")
    return len(entry) if isinstance(entry, list) else 0


@router.get("/meta/{branch_id}")
async def meta_verify(
    branch_id: int,
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
) -> Response:
    """Meta subscription handshake: echo hub.challenge iff verify_token matches."""
    expected = _verify_token_for(branch_id)
    if expected and hub_verify_token == expected and hub_challenge is not None:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/meta/{branch_id}")
async def meta_ingest(branch_id: int, request: Request) -> dict[str, int]:
    """Accept a Graph webhook payload, validate shape, ack fast (ingest is async)."""
    payload = await request.json()
    if not isinstance(payload, dict):
        return {"accepted": 0}
    return {"accepted": _count_entries(payload)}

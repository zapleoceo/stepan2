"""Inbound channel webhooks. Handlers stay thin: verify signature/parse → ack fast.
Actual ingest is enqueued downstream; here we only authenticate + validate shape."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_token_for(branch_id: int) -> str | None:
    """Per-branch Meta verify token from env (STEPAN2_META_VERIFY_TOKEN_<branch_id>)."""
    return os.environ.get(f"STEPAN2_META_VERIFY_TOKEN_{branch_id}")


def _app_secret_for(branch_id: int) -> str | None:
    """Per-branch Meta app secret used to sign payloads (X-Hub-Signature-256)."""
    return os.environ.get(f"STEPAN2_META_APP_SECRET_{branch_id}")


def _signature_ok(branch_id: int, raw: bytes, header: str) -> bool:
    """True iff X-Hub-Signature-256 matches HMAC-SHA256(app_secret, raw_body).

    Fail-closed: an unconfigured branch secret or a missing/foreign signature is
    rejected — we never trust an unsigned payload (it can forge any lead/message)."""
    secret = _app_secret_for(branch_id)
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def _count_entries(payload: dict[str, Any]) -> int:
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
    if (
        expected and hub_verify_token is not None
        and hmac.compare_digest(hub_verify_token, expected)
        and hub_challenge is not None
    ):
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=status.HTTP_403_FORBIDDEN)


@router.post("/meta/{branch_id}")
async def meta_ingest(branch_id: int, request: Request) -> Any:
    """Authenticate the X-Hub-Signature, validate shape, ack fast (ingest is async)."""
    raw = await request.body()
    if not _signature_ok(branch_id, raw, request.headers.get("X-Hub-Signature-256", "")):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    try:
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {"accepted": 0}
    if not isinstance(payload, dict):
        return {"accepted": 0}
    return {"accepted": _count_entries(payload)}

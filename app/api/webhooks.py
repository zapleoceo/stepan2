"""Inbound channel webhooks. Handlers stay thin: verify signature → parse → enqueue → ack fast.

Meta retries an unacknowledged delivery aggressively and disables an endpoint that answers
slowly, so nothing here touches the DB, Graph or the model: the POST parses the body and hands
the result to the worker, which does the real ingest.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status

from app.adapters.queue import enqueue
from app.modules.meta.app_secret import app_secret_for
from app.modules.meta.webhook_parse import WebhookMessage, parse_meta_messages

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
_log = logging.getLogger(__name__)

WEBHOOK_JOB = "ingest_meta_webhook"


def _verify_token_for(branch_id: int) -> str | None:
    """Per-branch Meta verify token from env (STEPAN2_META_VERIFY_TOKEN_<branch_id>)."""
    return os.environ.get(f"STEPAN2_META_VERIFY_TOKEN_{branch_id}")


def _app_secret_for(branch_id: int) -> str:
    """The app secret this branch signs with — resolved in one shared place, so the webhook
    and the client connect flow can never read different values."""
    return app_secret_for(branch_id)


def _signature_ok(branch_id: int, raw: bytes, header: str) -> bool:
    """True iff X-Hub-Signature-256 matches HMAC-SHA256(app_secret, raw_body).

    Fail-closed: an unconfigured branch secret or a missing/foreign signature is
    rejected — we never trust an unsigned payload (it can forge any lead/message)."""
    secret = _app_secret_for(branch_id)
    if not secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


async def _dispatch(branch_id: int, messages: list[WebhookMessage]) -> None:
    """Hand the batch to the worker. The job id is the first native message id, so Meta's own
    retry of a delivery still in flight does not queue the work twice."""
    await enqueue(
        WEBHOOK_JOB,
        branch_id,
        [m.as_dict() for m in messages],
        job_id=f"metahook:{branch_id}:{messages[0].mid}",
    )


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
    """Authenticate the X-Hub-Signature, parse the messages, enqueue them, ack.

    `accepted` counts the MESSAGES queued for ingest. It used to count entries, which was
    honest about nothing: the payload was parsed for its length and then discarded."""
    raw = await request.body()
    if not _signature_ok(branch_id, raw, request.headers.get("X-Hub-Signature-256", "")):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    try:
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {"accepted": 0}
    if not isinstance(payload, dict):
        return {"accepted": 0}
    messages = parse_meta_messages(payload)
    if not messages:
        return {"accepted": 0}  # receipts, reactions, echoes — nothing to ingest
    try:
        await _dispatch(branch_id, messages)
    except Exception:  # noqa: BLE001 — see below; the exception type is the queue's, not ours
        # A 200 here would tell Meta the message was taken and end its retries, and the lead's
        # message would exist nowhere until the reconcile poll happens to see it. Answering 503
        # keeps the delivery on Meta's retry schedule, which is the durability we want.
        _log.exception("webhook: cannot enqueue %d message(s) for branch=%s", len(messages),
                       branch_id)
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return {"accepted": len(messages)}

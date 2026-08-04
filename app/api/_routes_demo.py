"""Public demo chat for the landing — Stepan sells ITSELF to a visiting business owner.

Thin on purpose. The prompt is not here: the site is its own branch (app/modules/website),
assembled by the same composer + CRAFT the DM path uses, so this route owns only what is
HTTP — the abuse guards, the session token and the owner ping.

What it no longer does is trust the client. It used to accept the whole history including
`assistant` turns and hand that to the owner notification, so a visitor could put any words
in Stepan's mouth and have the forged transcript Telegrammed to the owner as a real lead. The
server now owns the conversation; the request carries one new user message and nothing else.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from app.adapters.db.session import session_scope
from app.api._demo_lead import maybe_notify
from app.modules.website import session as convo_store
from app.modules.website.branch import ensure_website_branch
from app.modules.website.chat import generate_reply

router = APIRouter()
_log = logging.getLogger(__name__)

# Abuse guard: this endpoint is PUBLIC (no auth) and calls a paid-capable broker capability,
# so an open loop could burn provider quota / drive spend (see the burn incidents). Cap the
# per-caller rate AND the total concurrent broker calls from the demo. In-process (per uvicorn
# worker) — enough to stop a flood without standing up Redis for a landing gimmick.
_RATE_WINDOW_S = 60.0
_RATE_MAX = 20                # requests per caller per window
_GLOBAL_INFLIGHT_MAX = 8      # concurrent demo broker calls across all callers (this worker)
_MAX_DISTINCT_KEYS = 5000
_hits: dict[str, deque[float]] = defaultdict(deque)
_inflight = 0

_BUSY = "One sec — a lot of people are chatting with me right now. Try again in a moment. 🎩"
_FALLBACK = "Sorry, I glitched for a second — say that again?"


def _rate_key(request: Request) -> str:
    """The one address in this request a browser cannot choose.

    X-Forwarded-For is a LIST, and only its tail is ours. Our reverse proxy APPENDS the peer
    it actually accepted the connection from, so the last element is written by us and the
    earlier ones are whatever the client typed into the header. The previous key was
    `xff.split(",")[0]` — the FIRST element — i.e. a string the attacker supplies, so a fresh
    "IP" per request defeated the limiter completely on a public endpoint that calls a paid
    capability. Take the last hop instead; with no proxy in front there is no header and the
    socket peer is already the truth."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        last = xff.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "?"


def _rate_ok(key: str) -> bool:
    now = time.monotonic()
    dq = _hits[key]
    while dq and now - dq[0] > _RATE_WINDOW_S:
        dq.popleft()
    if len(dq) >= _RATE_MAX:
        return False
    dq.append(now)
    if len(_hits) > _MAX_DISTINCT_KEYS:  # bound memory: sweep expired buckets
        for k in list(_hits):
            d = _hits[k]
            while d and now - d[0] > _RATE_WINDOW_S:
                d.popleft()
            if not d:
                del _hits[k]
    return True


@router.post("/demo/chat")
async def demo_chat(request: Request) -> JSONResponse:
    if not _rate_ok(_rate_key(request)):
        return JSONResponse({"reply": _BUSY}, status_code=429)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 — a malformed body is a client bug, not ours
        return JSONResponse({"reply": _FALLBACK})
    if not isinstance(payload, dict):
        return JSONResponse({"reply": _FALLBACK})
    message = str(payload.get("message") or "").strip()[:convo_store.MAX_CHARS]
    if not message:
        return JSONResponse({"reply": _FALLBACK})
    token = payload.get("session")
    convo = convo_store.open_conversation(token if isinstance(token, str) else None)

    global _inflight
    if _inflight >= _GLOBAL_INFLIGHT_MAX:  # atomic: no await between check and increment
        return JSONResponse({"reply": _BUSY, "session": convo.token}, status_code=429)
    _inflight += 1
    # The turn is generated against a candidate transcript and only committed to the stored
    # one when it produced an answer. A failed turn that had already appended the visitor's
    # line would leave it there, and their retry would send it a second time.
    turns = [*convo.turns, ("user", message)]
    try:
        async with session_scope() as db:
            branch_id = await ensure_website_branch(db)
            reply = await generate_reply(db, branch_id, turns)
    except Exception:  # noqa: BLE001 — a broken turn must not take the landing page down
        _log.exception("website demo turn failed")
        reply = ""
    finally:
        _inflight -= 1
    if not reply:
        return JSONResponse({"reply": _FALLBACK, "session": convo.token})
    convo_store.record(convo, "user", message)
    convo_store.record(convo, "assistant", reply)

    # Background (don't delay the reply): if this turn tipped the visitor into ready-to-buy
    # with a contact, DM the owner. Starlette's BackgroundTask runs AFTER the response is sent
    # and keeps a strong reference — unlike a bare asyncio.create_task, which the GC can drop
    # before it runs. maybe_notify self-gates + never raises.
    #
    # The transcript handed over is the SERVER's, so every "Степан:" line in the owner's card
    # is text this process generated. That is the fix for the forged-lead hole.
    history = [{"role": role, "content": text} for role, text in convo.turns]
    return JSONResponse({"reply": reply, "session": convo.token},
                        background=BackgroundTask(maybe_notify, history))

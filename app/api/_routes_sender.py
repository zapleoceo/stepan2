"""Inbound callback from the CRM's sender: a WhatsApp message a lead just wrote.

Stage one, deliberately. This accepts, authenticates, deduplicates and RECORDS — it does not
reply yet, because replying needs the sender MCP we have not been given, and the mapping of
their project/branch ids onto ours, which nobody has written down. Standing it up now buys two
things: their side gets something to test against, and we get to see the real payload — the
phone format above all, since the same Indonesian lead also writes to us on Instagram and the
two have to merge into one lead by number.

Closed by default. Callers must present the shared secret, or come from a listed address. With
neither configured the endpoint refuses everything: an open endpoint here means a stranger can
post a fake "inbound" and have Stepan answer a real customer in the business's name, on our
model budget. Their side sends no signature today (docs/sender-integration-questions.md, item
1), so until that is agreed the allowlist is the way in.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections import deque

from fastapi import APIRouter, Request, Response, status

from app.config import settings
from app.domain.clock import utc_now

router = APIRouter(prefix="/api/v1/sender", tags=["sender"])
_log = logging.getLogger(__name__)

# The fields we consume. Their side sends the whole SendMessage row; everything outside this
# set is noise we deliberately ignore rather than store (docs list what was dropped and why).
_WANTED = (
    "id", "external_id", "from", "message", "chanel",
    "project_id", "branch_id", "conversation_id", "chat_id",
    "from_name", "user_id", "attachment",
)

# Seen message ids, newest last. Their side already dedupes, but a retry or a restart on
# either end can repeat a callback, and answering a lead twice is the visible failure.
# In-process on purpose at this stage: nothing is persisted yet, so a restart forgetting is
# harmless. When replies start going out this MUST move to the DB.
_SEEN: deque[str] = deque(maxlen=5000)
_SEEN_SET: set[str] = set()

# Last few payloads, for reading the shape their side actually sends while we integrate.
_RECENT: deque[dict] = deque(maxlen=50)


def _authorised(request: Request, raw: bytes) -> bool:
    """True if this really came from sender.

    `sender_callback_open` short-circuits all of it, and is safe ONLY because this endpoint
    records and nothing else: a forged payload starts no reply, reaches no customer and spends
    no model budget. It exists so their side can test delivery before we have agreed on a
    secret. The reply path must refuse to run while it is on — see the guard at the top of
    whatever eventually sends, and docs/sender-endpoint-for-victor.md.

    Two accepted proofs otherwise, because their side has not committed to one yet:
      * a signature header — HMAC-SHA256 of the raw body under the shared secret, compared in
        constant time. This is what we want long term.
      * a bearer token equal to the shared secret, if they would rather not compute an HMAC.
      * the caller's address being on the allowlist, as the interim they can do TODAY without
        touching their code.
    No secret and no allowlist configured means nobody is authorised — refusing every call is
    the correct behaviour for an endpoint that can make us message real customers."""
    if settings().sender_callback_open:
        return True

    secret = settings().sender_callback_secret
    if secret:
        header = request.headers.get("X-Sender-Signature", "")
        if header:
            expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            candidate = header[len("sha256="):] if header.startswith("sha256=") else header
            if hmac.compare_digest(expected, candidate):
                return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], secret):
            return True

    allow = {ip.strip() for ip in settings().sender_callback_ips.split(",") if ip.strip()}
    client = request.client.host if request.client else ""
    return bool(allow and client in allow)


def _remember(external_id: str) -> bool:
    """True if this id is new. Evicting from the deque drops it from the set with it."""
    if external_id in _SEEN_SET:
        return False
    if len(_SEEN) == _SEEN.maxlen:
        _SEEN_SET.discard(_SEEN[0])
    _SEEN.append(external_id)
    _SEEN_SET.add(external_id)
    return True


@router.post("/inbound-callback")
async def inbound_callback(request: Request) -> Response:
    """Accept one inbound message. Fast, always — their side times out in 5 seconds.

    Answers 200 for anything it accepted OR deliberately ignored, and only 403 for a caller it
    could not authenticate. A 4xx for a duplicate would make their retry logic fight ours."""
    raw = await request.body()
    if not _authorised(request, raw):
        _log.warning("sender callback refused: no valid signature and address not allowlisted")
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    if settings().sender_callback_open:
        # Loud on every call, not once at boot: this is a temporary state and the log is what
        # makes it impossible to forget it is still on weeks later.
        _log.warning("sender callback is UNAUTHENTICATED (sender_callback_open) — "
                     "record-only mode, must be turned off before replies go out")

    form = await request.form()
    data = {k: str(form[k]) for k in _WANTED if k in form}

    external_id = data.get("external_id", "").strip()
    if not external_id:
        # Without it we cannot tell a retry from a new message, and answering twice is worse
        # than not answering: accept, record, and let it be visible in the log.
        _log.warning("sender callback without external_id: %s", _safe(data))
        _RECENT.append({"at": utc_now().isoformat(), "no_external_id": True, **data})
        return Response(status_code=status.HTTP_200_OK)

    if not _remember(external_id):
        _log.info("sender callback duplicate, ignored: external_id=%s", external_id)
        return Response(status_code=status.HTTP_200_OK)

    _RECENT.append({"at": utc_now().isoformat(), **data})
    # INFO on purpose while integrating: this is the record of what their side actually sends,
    # and the phone format in `from` is the open question that decides whether a WhatsApp lead
    # merges with the same person's Instagram thread.
    _log.info("sender inbound: %s", _safe(data))
    return Response(status_code=status.HTTP_200_OK)


def _safe(data: dict) -> dict:
    """The payload with the message body shortened — a lead's text belongs in the DB, not in
    a log line, and a long one buries the fields we are actually reading."""
    out = dict(data)
    text = out.get("message") or ""
    if len(text) > 80:
        out["message"] = text[:80] + f"…(+{len(text) - 80})"
    return out


def recent() -> list[dict]:
    """What arrived lately — for checking the integration against their side."""
    return list(_RECENT)


def _reset_for_tests() -> None:
    _SEEN.clear()
    _SEEN_SET.clear()
    _RECENT.clear()


__all__ = ["recent", "router"]

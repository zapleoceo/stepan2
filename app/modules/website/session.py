"""Server-owned transcripts for the site chat, keyed by a signed session token.

The endpoint used to accept the whole history from the browser, `assistant` turns included,
and the owner-notification fired on that transcript. So a visitor could POST any words they
liked as Stepan's, add a contact, and have the forged conversation Telegrammed to the owner as
a real lead. Now the client sends one thing — the text the person just typed — and every
assistant line in the transcript is one this process generated.

In memory, not in the database: the owner's S6 decision was explicitly no lead rows for
anonymous visitors. A worker restart drops the conversations in flight, which costs a visitor
their context and nothing else; persisting them would cost exactly what was refused.
"""
from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.api._session import sign, verify
from app.config import settings

# A conversation is abandoned long before this; the ceiling is here so a token minted hours
# ago cannot be replayed to keep one transcript alive for ever.
MAX_AGE_S = 2 * 3600
# Turns kept per conversation — enough context to sell, bounded so one visitor cannot grow a
# single entry without limit.
MAX_TURNS = 24
# Live conversations held at once, oldest evicted first. Bounds the memory a public endpoint
# can be made to allocate — and the bound is the product of all three constants, so it is
# written out: 500 x 24 x 2000 = 24M characters, about 24 MB of str payload (plus per-object
# overhead) resident in the API container in the worst case, for a landing-page widget. It was
# 2000 sessions, i.e. ~96 MB, a number nobody had multiplied out. 500 concurrent live
# conversations on this page is already far beyond anything it has seen; a visitor evicted
# early loses context and is handed a fresh token, not an error.
MAX_SESSIONS = 500
MAX_CHARS = 2000  # per message


@dataclass
class Conversation:
    token: str
    turns: list[tuple[str, str]] = field(default_factory=list)
    touched_at: float = field(default_factory=time.monotonic)


_live: OrderedDict[str, Conversation] = OrderedDict()


def _secret() -> str:
    return settings().session_secret or settings().secret_key


def mint_token() -> str:
    """A fresh session token. Signed so an unknown token cannot be smuggled in as a known one
    after an eviction — an id we did not issue must never open an empty transcript that then
    accumulates under a name the client chose."""
    return sign({"sid": secrets.token_urlsafe(12), "iat": int(time.time())}, _secret())


def open_conversation(token: str | None) -> Conversation:
    """The conversation this token names, or a brand-new one.

    A token that fails the signature, has expired, or has been evicted starts a fresh
    conversation with a fresh token rather than an error: the visitor is mid-sentence on a
    marketing page, and 'your session expired' is a lost lead."""
    if token and verify(token, _secret(), MAX_AGE_S) is not None:
        convo = _live.get(token)
        if convo is not None:
            convo.touched_at = time.monotonic()
            _live.move_to_end(token)
            return convo
    convo = Conversation(token=mint_token())
    _live[convo.token] = convo
    _evict()
    return convo


def record(convo: Conversation, role: str, text: str) -> None:
    """Append one turn. `role` is chosen by the caller, never by the request body — that is
    the whole fix: the client can only ever cause a 'user' turn."""
    convo.turns.append((role, text[:MAX_CHARS]))
    del convo.turns[:-MAX_TURNS]
    convo.touched_at = time.monotonic()


def _evict() -> None:
    cutoff = time.monotonic() - MAX_AGE_S
    for token in [t for t, c in _live.items() if c.touched_at < cutoff]:
        del _live[token]
    while len(_live) > MAX_SESSIONS:
        _live.popitem(last=False)

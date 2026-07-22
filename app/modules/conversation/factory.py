"""Which reply engine a branch runs — the single place the v2/v3 choice is made.

Three call sites build a reply service (the worker's reply job, the sim harness, and the
manager's "suggest a reply" panel). Putting the choice here keeps them identical and means
flipping a branch to v3 is one setting, not a deploy — and flipping it back is too.

An unknown value resolves to v2 in BranchSettings itself, so this only ever sees v2 or v3.
"""
from __future__ import annotations

from .reply import ReplyService
from .reply_v3 import ReplyServiceV3

V3 = "v3"


def build_reply_service(*args, engine: str = "v2", **kwargs) -> ReplyService:  # noqa: ANN002, ANN003
    """A reply service for this branch. ReplyServiceV3 subclasses ReplyService, so the caller
    never has to know which one it got."""
    service = ReplyServiceV3 if engine == V3 else ReplyService
    return service(*args, **kwargs)

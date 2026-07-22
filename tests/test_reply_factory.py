"""The v2/v3 switch — one place, so flipping a branch is a setting and not a deploy.

The substitutability assertion matters most: three call sites (the worker's reply job, the sim
harness, the manager's suggest panel) hold whatever this returns and call decide/enqueue on it
without knowing which engine they got. If v3 ever stopped being a drop-in, the worker would
break at runtime on a branch nobody had tested.
"""
from __future__ import annotations

import inspect

from app.modules.conversation.factory import build_reply_service
from app.modules.conversation.reply import ReplyService
from app.modules.conversation.reply_v3 import ReplyServiceV3
from app.modules.settings.service import _parse


class _LLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return "{}", {}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


def _build(session, engine: str):  # noqa: ANN001, ANN202
    return build_reply_service(session, 1, _LLM(), object(), engine=engine)


async def test_v2_is_the_default(db_session) -> None:  # noqa: ANN001
    service = build_reply_service(db_session, 1, _LLM(), object())
    assert type(service) is ReplyService


async def test_each_engine_maps_to_its_service(db_session) -> None:  # noqa: ANN001
    assert type(_build(db_session, "v2")) is ReplyService
    assert type(_build(db_session, "v3")) is ReplyServiceV3


async def test_an_unexpected_engine_still_replies(db_session) -> None:  # noqa: ANN001
    """Belt and braces: BranchSettings already sanitises this, but the factory must not be
    the thing that leaves a branch with no reply service at all."""
    assert isinstance(_build(db_session, "v4"), ReplyService)


async def test_the_setting_and_the_factory_agree_on_the_vocabulary(db_session) -> None:  # noqa: ANN001
    """Whatever _parse can produce, the factory must accept."""
    for raw in ("v2", "v3", "", "nonsense"):
        engine = _parse({"reply_engine": raw}).reply_engine
        assert isinstance(_build(db_session, engine), ReplyService)


def test_v3_is_a_drop_in_for_v2() -> None:
    """Delivery — enqueue, bubbles, stage events, hand-off, outbox — is inherited, not
    reimplemented, so every caller can swap the two without knowing which it holds."""
    assert issubclass(ReplyServiceV3, ReplyService)
    assert inspect.signature(ReplyServiceV3.decide) == inspect.signature(ReplyService.decide)
    for method in ("enqueue_reply", "_apply_decision", "_handoff"):
        assert hasattr(ReplyServiceV3, method)


async def test_v3_brings_its_own_dossier_store(db_session) -> None:  # noqa: ANN001
    service = _build(db_session, "v3")
    assert service.dossiers.branch_id == 1
    assert not hasattr(_build(db_session, "v2"), "dossiers")

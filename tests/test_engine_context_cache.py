"""DecisionEngine memoizes the branch's knowledge within a turn.

A DecisionEngine lives for ONE lead-turn, and a turn can call back in more than once (the
money-gate rewrite, the critic rewrite). Without memoization each of those re-ran assembly —
identically — so the same context was rebuilt two or three times per turn.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.engine import DecisionEngine

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _SpyKnowledge:
    def __init__(self) -> None:
        self.calls = 0

    async def knowledge_context(self, product_slug, *, query, thread_id, light=False,  # noqa: ANN001, ANN003
                                lead_type=None, has_open_objection=False):
        self.calls += 1
        return f"CTX[{query}|light={light}]"


class _FakeLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return '{"reply": "ok", "move": "give_value", "stage": "qualifying"}', {"cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _thread(s) -> tuple[int, int]:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(th)
    await s.flush()
    s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id="m1",
                  direction="in", sent_by="lead", text="berapa harga kursusnya",
                  occurred_at=_NOW))
    await s.flush()
    return b.id, th.id


async def test_context_is_built_once_per_turn(db_session) -> None:  # noqa: ANN001
    """A rewrite pass must not pay for assembly again — same dialog, same context."""
    bid, tid = await _thread(db_session)
    spy = _SpyKnowledge()
    engine = DecisionEngine(db_session, bid, _FakeLLM(), spy)
    ctx = await engine.prepare(tid, workflow="reply")
    assert ctx is not None

    first = await engine.kb_context(ctx, tid, light=False)
    again = await engine.kb_context(ctx, tid, light=False)
    once_more = await engine.kb_context(ctx, tid, light=False)

    assert spy.calls == 1
    assert first == again == once_more


async def test_a_lighter_context_is_cached_separately(db_session) -> None:  # noqa: ANN001
    """A follow-up asks for a different shape, so it must not be served the reply's cache."""
    bid, tid = await _thread(db_session)
    spy = _SpyKnowledge()
    engine = DecisionEngine(db_session, bid, _FakeLLM(), spy)
    ctx = await engine.prepare(tid, workflow="reply")
    assert ctx is not None

    await engine.kb_context(ctx, tid, light=False)
    await engine.kb_context(ctx, tid, light=True)
    assert spy.calls == 2


async def test_the_last_built_context_is_exposed_for_the_money_gate(db_session) -> None:  # noqa: ANN001
    """The gate checks a draft's figures against exactly the knowledge the model was given."""
    bid, tid = await _thread(db_session)
    engine = DecisionEngine(db_session, bid, _FakeLLM(), _SpyKnowledge())
    ctx = await engine.prepare(tid, workflow="reply")
    assert ctx is not None

    built = await engine.kb_context(ctx, tid, light=False)
    assert engine.last_context == built

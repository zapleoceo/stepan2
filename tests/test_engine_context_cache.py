"""DecisionEngine memoizes knowledge_context within a turn — a regen (guard correction,
dedup, fast→smart) must not re-embed + re-scan the identical retrieval query."""
from __future__ import annotations

from datetime import UTC, datetime

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.engine import DecisionEngine

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _SpyKnowledge:
    def __init__(self) -> None:
        self.calls = 0

    async def knowledge_context(self, product_slug, *, query, thread_id, light=False):  # noqa: ANN001, ANN003
        self.calls += 1
        return f"CTX[{query}]"


class _FakeLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return '{"reply": "ok", "stage": "qualifying"}', {"model": "f", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _thread(s) -> tuple[int, int]:
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
                  direction="in", sent_by="lead", text="berapa harga kursusnya", occurred_at=_NOW))
    await s.flush()
    return b.id, th.id


async def test_complete_reuses_context_across_regens(db_session) -> None:
    bid, tid = await _thread(db_session)
    spy = _SpyKnowledge()
    engine = DecisionEngine(db_session, bid, _FakeLLM(), spy)
    ctx = await engine.prepare(tid, workflow="reply")
    assert ctx is not None
    # first decision + two regens of the SAME turn (same dialog → same retrieval query)
    await engine.complete(ctx, tid, lang="id", workflow="reply", capability="chat:fast")
    await engine.complete(ctx, tid, lang="id", workflow="reply", capability="chat:smart",
                          extra_user_msg="[System: fix this]")
    await engine.complete(ctx, tid, lang="id", workflow="reply", capability="chat:smart",
                          extra_user_msg="[System: fix again]")
    assert spy.calls == 1  # built once, reused on both regens — no re-embed / re-scan


async def test_followup_light_context_is_cached_separately(db_session) -> None:
    """A live reply and a followup nudge use different (light) contexts — distinct cache keys."""
    bid, tid = await _thread(db_session)
    spy = _SpyKnowledge()
    engine = DecisionEngine(db_session, bid, _FakeLLM(), spy)
    ctx = await engine.prepare(tid, workflow="reply")
    await engine.complete(ctx, tid, lang="id", workflow="reply")
    await engine.complete(ctx, tid, lang="id", workflow="followup")  # light=True → new key
    assert spy.calls == 2

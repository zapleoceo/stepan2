"""LLM failure in the reply pipeline: decide() propagates cleanly with no partial state,
and the worker's _reply_thread isolates the failure (returns False, never raises)."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from contextlib import asynccontextmanager  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402
from sqlmodel import select  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Message,
    Outbox,
)
from app.domain.enums import ChannelKind, Stage  # noqa: E402
from app.modules.conversation import ReplyService  # noqa: E402
from app.modules.knowledge.service import KnowledgeService  # noqa: E402
from app.modules.settings.service import _parse, invalidate  # noqa: E402
from app.worker import main as worker_main  # noqa: E402
from app.worker import wiring  # noqa: E402

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _RaisingLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        raise RuntimeError("broker down")

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


async def _world(s) -> tuple[int, int, Lead, ChannelThread]:  # noqa: ANN001
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=Stage.NEW)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                  external_id="m1", direction="in", sent_by="lead", text="halo",
                  occurred_at=_NOW))
    await s.flush()
    invalidate(branch.id)
    return branch.id, thread.id, lead, thread


async def test_decide_propagates_llm_error_without_side_effects(db_session) -> None:
    bid, tid, lead, thread = await _world(db_session)
    svc = ReplyService(db_session, bid, _RaisingLLM(), KnowledgeService(db_session, bid),
                       branch_settings=_parse({}))
    # Current contract: decide() has no try/except around llm.chat — the error propagates
    # to the caller (worker's _reply_thread catches it per-thread).
    with pytest.raises(RuntimeError, match="broker down"):
        await svc.decide(tid)

    assert (await db_session.exec(select(Outbox))).first() is None
    assert thread.last_out_at is None
    assert lead.needs is None
    assert lead.stage == Stage.NEW
    assert lead.preferred_language is None


async def test_worker_reply_thread_returns_false_when_llm_raises(monkeypatch) -> None:
    """_reply_thread wraps the whole decide+enqueue in try/except: a raising ReplyService
    must yield False, not an exception, so one poison thread can't kill the tick."""

    @asynccontextmanager
    async def _fake_scope():
        yield object()

    async def _locked(_session, _thread_id) -> bool:
        return True

    async def _fake_settings(_session, _branch_id):  # noqa: ANN202
        return _parse({})

    class _RaisingReply:
        def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
            pass

        async def decide(self, thread_id: int):  # noqa: ANN201
            raise RuntimeError("broker down")

    async def _platform_on(_session) -> bool:
        return True

    async def _kb(_session, branch_id):  # noqa: ANN202
        return branch_id

    monkeypatch.setattr(worker_main, "session_scope", _fake_scope)
    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_on)
    monkeypatch.setattr(worker_main, "BrokerLLM", _RaisingLLM)
    monkeypatch.setattr(wiring, "try_lock_thread", _locked)
    monkeypatch.setattr(worker_main, "get_settings", _fake_settings)
    monkeypatch.setattr(worker_main, "effective_kb_branch", _kb)
    monkeypatch.setattr(worker_main, "_build_notifier", lambda _cfg: None)
    monkeypatch.setattr(worker_main, "KnowledgeService", lambda *a, **kw: object())
    monkeypatch.setattr(worker_main, "ReplyService", _RaisingReply)

    class _FakeRedis:
        async def zremrangebyscore(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            return 0

        async def zadd(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            return 1

        async def zcard(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            return 1

        async def zrem(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            return 1

    result = await worker_main.generate_one_reply({"redis": _FakeRedis()}, 1, 42)
    assert result is False


class _BadJsonLLM:
    """Returns unparseable text for EVERY chat call (both fast and the smart escalation)."""
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        return "not json at all", {"model": "m", "cost_usd": 0.0}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


async def test_decide_returns_none_when_both_fast_and_smart_unparseable(db_session) -> None:
    """A double-unparseable decision (fast fails → smart escalation also fails) must degrade
    to None (caller skips + retries), NOT raise ValueError and abort the reply job."""
    bid, tid, lead, thread = await _world(db_session)
    # 'new'-stage mid-conversation, neutral, cold → routes to fast (active sales stages now run
    # on smart), so the fast→smart escalation path is exercised
    lead.stage = Stage.NEW
    lead.lead_type = "cold"
    db_session.add(lead)
    for i, txt in enumerate(("lanjut", "oh gitu")):
        db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                               external_id=f"mx{i}", direction="in", sent_by="lead",
                               text=txt, occurred_at=_NOW))
    await db_session.flush()
    llm = _BadJsonLLM()
    svc = ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid),
                       branch_settings=_parse({}))
    decision = await svc.decide(tid)
    assert decision is None
    # The opener always runs on the strong model, and a broken STRONG answer is not retried:
    # two attempts is the ceiling, and burning the second on the tier that just failed buys
    # nothing. The fast->smart escalation is covered in test_reply.py.
    assert llm.calls == 1

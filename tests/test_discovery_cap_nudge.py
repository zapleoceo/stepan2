"""Discovery cap: a static KB rule alone wasn't reliable (live testing kept seeing a 3rd/4th
discovery question before a direct answer) — decide() now injects a turn-aware nudge the
moment the cap is exceeded, the same mechanism the reply-guard uses for its correction."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.reply import _DISCOVERY_TURN_CAP
from app.modules.knowledge.service import KnowledgeService

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _SpyLLM:
    def __init__(self) -> None:
        self.last_messages: list | None = None

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.last_messages = messages
        return json.dumps({"reply": "ok", "stage": "qualifying"}), \
            {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _thread_with_turns(s, n_inbound: int) -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)  # no needs captured
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(th)
    await s.flush()
    for i in range(n_inbound):
        s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id=f"m{i}",
                      direction="in", sent_by="lead", text="halo", occurred_at=_NOW))
    await s.flush()
    return b.id, th.id


async def test_no_nudge_within_cap(db_session) -> None:
    bid, tid = await _thread_with_turns(db_session, _DISCOVERY_TURN_CAP)
    llm = _SpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert llm.last_messages[-1]["role"] != "user" or "discovery questions for" \
        not in llm.last_messages[-1]["content"]


async def test_nudge_injected_past_cap_without_captured_needs(db_session) -> None:
    bid, tid = await _thread_with_turns(db_session, _DISCOVERY_TURN_CAP + 1)
    llm = _SpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    last = llm.last_messages[-1]
    assert last["role"] == "user"
    assert "do NOT ask another discovery question this turn" in last["content"]


async def test_non_target_nudge_wraps_up_instead_of_re_pitching(db_session) -> None:
    """Chat 2027: a domain seller kept getting pitched Vibe Coding turn after turn even
    though the model had already classified them non_target — once that classification
    is already on the lead from a prior turn, stop re-engaging."""
    bid, tid = await _thread_with_turns(db_session, 3)
    lead = (await db_session.execute(
        select(Lead).where(Lead.branch_id == bid))).scalars().first()
    lead.lead_type = "non_target"
    db_session.add(lead)
    await db_session.flush()
    llm = _SpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    last = llm.last_messages[-1]
    assert last["role"] == "user"
    assert "already classified non_target" in last["content"]
    assert "Do NOT keep pitching" in last["content"]


async def test_no_non_target_nudge_for_a_normal_lead(db_session) -> None:
    bid, tid = await _thread_with_turns(db_session, 3)
    llm = _SpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    last = llm.last_messages[-1]
    assert last["role"] != "user" or "non_target" not in last["content"]

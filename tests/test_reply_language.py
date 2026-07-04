"""Reply language: KB stays any-language, the bot replies in the branch default unless the
lead switched — then reply_language is parsed, persisted on the lead, and wins next time."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import parse_decision
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import _parse

_NOW = datetime.now(UTC).replace(tzinfo=None)


def test_parse_decision_reads_reply_language() -> None:
    base = {"reply": "hi", "stage": "qualifying"}
    assert parse_decision(json.dumps({**base, "reply_language": "EN"})).reply_language == "en"
    assert parse_decision(json.dumps({**base, "reply_language": "русский"})).reply_language \
        is None  # non-ascii / too long → ignored
    assert parse_decision(json.dumps(base)).reply_language is None


class _LangLLM:
    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return json.dumps({"reply": "ok", "stage": "qualifying", "reply_language": "ru"}), \
            {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


class _SpyLLM:
    """Never fills in reply_language — the exact live failure: the model forgets to
    self-report the switch, so the OLD code path never persisted it."""

    def __init__(self) -> None:
        self.seen_lang: str | None = None

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.seen_lang = messages[0]["content"]  # system prompt carries the {lang} token
        return json.dumps({"reply": "ok", "stage": "qualifying"}), \
            {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def _thread(s, *, pref: str | None = None) -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, preferred_language=pref)
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(th)
    await s.flush()
    s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id="m1",
                  direction="in", sent_by="lead", text="halo", occurred_at=_NOW))
    await s.flush()
    return b.id, th.id


def _svc(s, bid: int, llm: Any) -> ReplyService:
    return ReplyService(s, bid, llm, KnowledgeService(s, bid, llm),
                        branch_settings=_parse({}), notifier=None)


async def test_lang_ladder_prefers_lead(db_session) -> None:
    bid, _ = await _thread(db_session, pref="en")
    svc = _svc(db_session, bid, _LangLLM())
    lead = (await db_session.exec(select(Lead))).first()
    assert await svc._lang(lead) == "en"      # lead preference wins
    assert await svc._lang(None) == "id"      # else branch default


async def test_decision_persists_lead_language(db_session) -> None:
    bid, tid = await _thread(db_session)
    svc = _svc(db_session, bid, _LangLLM())
    decision = await svc.decide(tid)
    assert decision is not None and decision.reply_language == "ru"
    await svc.enqueue_reply(tid, decision)
    lead = (await db_session.exec(select(Lead))).first()
    assert lead.preferred_language == "ru"    # lead switched → remembered


async def test_cyrillic_in_lead_text_overrides_stale_default_immediately(db_session) -> None:
    """REGRESSION: live thread 452 kept drifting back to Bahasa mid-conversation because
    persistence relied ENTIRELY on the model self-reporting reply_language, and it doesn't
    reliably do that every turn. A lead writing in Cyrillic must get a Russian reply and a
    persisted preference on THIS turn, even when the model's own decision says nothing
    about language at all."""
    bid, tid = await _thread(db_session)  # branch default is "id", lead has no preference yet
    b = (await db_session.exec(select(Branch))).first()
    thread = (await db_session.exec(select(ChannelThread).where(ChannelThread.id == tid))).one()
    lead_before = (await db_session.exec(select(Lead))).first()
    db_session.add(Message(branch_id=b.id, thread_id=tid, channel_id=thread.channel_id,
                           external_id="m2", direction="in", sent_by="lead",
                           text="Привет, у вас дорого", occurred_at=_NOW))
    await db_session.flush()

    llm = _SpyLLM()
    svc = _svc(db_session, bid, llm)
    decision = await svc.decide(tid)

    assert decision is not None and decision.reply_language is None  # model said nothing
    assert "'ru'" in llm.seen_lang  # THIS turn's prompt already asked for Russian
    lead_after = (await db_session.exec(select(Lead))).first()
    assert lead_after.id == lead_before.id
    assert lead_after.preferred_language == "ru"  # persisted without waiting on self-report

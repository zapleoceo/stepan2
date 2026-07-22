"""v3 reply pipeline — one call over a dossier, and what it remembers afterwards.

v2 ran a draft through eight sequential rewrite passes that knew nothing of each other. The
tests here pin the replacement: generate once, learn once, escalate at most one tier, and never
lose a turn to a contract slip.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.adapters.channels.ig_parse import VOICE_PENDING_PH
from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.dossier import LeadDossier, Objection
from app.modules.conversation.reply_v3 import ReplyServiceV3
from app.modules.conversation.repository import DossierRepo
from app.modules.conversation.routing_v3 import FAST, SMART

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _LLM:
    """Records how it was called and replays scripted raw answers."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers) or [_answer()]
        self.capabilities: list[str] = []
        self.messages: list[list[dict]] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.capabilities.append(kw.get("capability", ""))
        self.messages.append(messages)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return answer, {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


class _Knowledge:
    async def knowledge_context(self, product_slug, **kw):  # noqa: ANN001, ANN003, ANN201
        return "KB FACTS"


def _answer(**over) -> str:  # noqa: ANN003
    payload = {"reply": "halo kak", "move": "answer_question", "stage": "qualifying"}
    payload.update(over)
    return json.dumps(payload)


async def _thread(s, *, texts: tuple[tuple[str, str], ...] = (("in", "halo"),),  # noqa: ANN001
                  needs: str | None = None, dossier: str | None = None) -> tuple[int, int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, needs=needs, dossier=dossier)
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(th)
    await s.flush()
    for i, (direction, text) in enumerate(texts):
        s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id=f"m{i}",
                      direction=direction, sent_by="lead" if direction == "in" else "bot",
                      text=text, occurred_at=_NOW))
    await s.flush()
    return b.id, th.id, lead.id


def _service(session, branch_id: int, llm: _LLM) -> ReplyServiceV3:  # noqa: ANN001
    return ReplyServiceV3(session, branch_id, llm, _Knowledge())


# ── the happy path ────────────────────────────────────────────────────────────

async def test_one_turn_is_one_model_call(db_session) -> None:  # noqa: ANN001
    """v2's worst case was twelve calls on one turn."""
    bid, tid, _ = await _thread(db_session)
    llm = _LLM()
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "halo kak"
    assert len(llm.capabilities) == 1


async def test_what_the_turn_learned_is_persisted(db_session) -> None:  # noqa: ANN001
    bid, tid, lid = await _thread(db_session)
    llm = _LLM(_answer(dossier={"role": "student", "pains": ["takut telat"]}))
    await _service(db_session, bid, llm).decide(tid)

    stored = await DossierRepo(db_session, bid).load(lid)
    assert stored.role == "student"
    assert stored.pains == ["takut telat"]


async def test_learning_accumulates_across_turns(db_session) -> None:  # noqa: ANN001
    """The v2 leak this closes: an objection omitted one turn used to vanish permanently."""
    bid, tid, lid = await _thread(
        db_session, dossier=LeadDossier(objections=[Objection("mahal")],
                                        pains=["takut telat"]).to_json())
    llm = _LLM(_answer(dossier={"desired_state": ["kerja remote"]}))
    await _service(db_session, bid, llm).decide(tid)

    stored = await DossierRepo(db_session, bid).load(lid)
    assert stored.open_objections() == ["mahal"]
    assert stored.pains == ["takut telat"]
    assert stored.desired_state == ["kerja remote"]


async def test_the_dossier_reaches_the_prompt_so_nothing_is_re_asked(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(
        db_session, dossier=LeadDossier(pains=["takut telat"],
                                        cases_used=["alumni Dimas"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)

    system = llm.messages[0][0]["content"]
    assert "takut telat" in system
    assert "ALREADY USED" in system and "alumni Dimas" in system


async def test_a_lead_with_only_legacy_needs_still_gets_its_context(db_session) -> None:  # noqa: ANN001
    """The switchover case — a v2 conversation continuing under v3 loses nothing."""
    from app.modules.conversation.needs import NeedsProfile
    bid, tid, _ = await _thread(
        db_session, needs=NeedsProfile(pains=["takut telat"], objections=["mahal"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)

    system = llm.messages[0][0]["content"]
    assert "takut telat" in system and "mahal" in system


# ── routing ───────────────────────────────────────────────────────────────────

async def test_the_opener_runs_on_the_strong_model(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session)
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)
    assert llm.capabilities == [SMART]


async def test_a_quiet_mid_conversation_turn_runs_cheap(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai kak"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring").to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)
    assert llm.capabilities == [FAST]


# ── failure handling: a turn is never lost to a contract slip ─────────────────

async def test_a_broken_cheap_answer_escalates_once_to_the_strong_model(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring").to_json())
    llm = _LLM("not json at all", _answer(reply="kembali normal"))
    decision = await _service(db_session, bid, llm).decide(tid)

    assert decision is not None
    assert decision.reply == "kembali normal"
    assert llm.capabilities == [FAST, SMART]


async def test_a_broken_strong_answer_is_not_retried_forever(db_session) -> None:  # noqa: ANN001
    """Two attempts is the ceiling — a third rewrite is what v2 did."""
    bid, tid, _ = await _thread(db_session)
    llm = _LLM("not json")
    assert await _service(db_session, bid, llm).decide(tid) is None
    assert len(llm.capabilities) == 1


async def test_a_turn_waiting_on_media_is_held_without_a_model_call(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session, texts=(("in", VOICE_PENDING_PH),))
    llm = _LLM()
    assert await _service(db_session, bid, llm).decide(tid) is None
    assert llm.capabilities == []


async def test_a_foreign_thread_is_invisible(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session)
    other = Branch(name="Other", lang="id")
    db_session.add(other)
    await db_session.flush()

    llm = _LLM()
    assert await _service(db_session, other.id, llm).decide(tid) is None
    assert llm.capabilities == []


async def test_the_chosen_move_is_kept_for_logging(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session)
    service = _service(db_session, bid, _LLM(_answer(move="quote_price")))
    await service.decide(tid)
    assert service.last_decision is not None
    assert service.last_decision.move == "quote_price"

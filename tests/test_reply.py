"""The reply pipeline — one generation over a dossier, the money gate, and what a turn
remembers. The scripted gates/critic/turn-notes were retired 2026-07-25 (the sim A/B that
retired them: agreements 6/10 vs 3/10, forced hand-offs 0/10 vs 8/10)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from app.adapters.channels.ig_parse import VOICE_PENDING_PH
from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.dossier import LeadDossier, Objection
from app.modules.conversation.reply import ReplyService
from app.modules.conversation.repository import DossierRepo
from app.modules.conversation.routing import FAST, SALES, SMART

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _LLM:
    """Records how it was called and replays scripted raw answers."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers) or [_answer()]
        self.capabilities: list[str] = []
        self.messages: list[list[dict]] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        # The discovery backstop (workflow="discovery") is a SEPARATE extraction call, not part
        # of the scripted reply sequence — return an empty extraction so it doesn't consume a
        # scripted answer and shift the rewrite responses these tests assert on.
        if kw.get("workflow") == "discovery":
            return '{"job_to_be_done":"","pains":[],"desired_state":[],"objections":[]}', \
                {"model": "fake", "cost_usd": 0.0}
        self.capabilities.append(kw.get("capability", ""))
        self.messages.append(messages)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        # request_id differs per call so a test can tell WHICH generation's meta ended up
        # on the bubble (the rewrite's, when the money gate fired).
        return answer, {"model": "fake", "cost_usd": 0.0,
                        "request_id": f"req{len(self.capabilities)}"}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


class _Knowledge:
    def __init__(self, context: str = "KB FACTS") -> None:
        self._context = context

    async def full_knowledge_context(self, lang=None):  # noqa: ANN001, ANN201
        return self._context

    async def knowledge_context(self, product_slug, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._context


# A dossier that already has_discovery() == True — used by tests whose intent is routing/
# call-count behaviour, not the discovery-extraction backstop, so the extra chat:fast pass
# never fires and the call-count assertions stay exact.
_DISCOVERED = LeadDossier(pains=["takut telat"], desired_state=["kerja remote"]).to_json()


def _answer(**over) -> str:  # noqa: ANN003
    payload = {"reply": "halo kak", "move": "answer_question", "stage": "qualifying"}
    payload.update(over)
    return json.dumps(payload)


async def _thread(s, *, texts: tuple[tuple[str, str], ...] = (("in", "halo"),),  # noqa: ANN001
                  needs: str | None = None, dossier: str | None = None) -> tuple[int, int, int]:
    """Unless the texts already carry an outbound, a prior bot greeting is prepended so the
    turn models mid-conversation state (a genuine FIRST turn is the opener module's regime)."""
    if not any(d == "out" for d, _ in texts):
        from app.modules.conversation.opener import AD_TAP_OPENER  # noqa: PLC0415
        texts = (("out", AD_TAP_OPENER), *texts)
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


def _service(session, branch_id: int, llm: _LLM, kb: str = "KB FACTS") -> ReplyService:  # noqa: ANN001
    return ReplyService(session, branch_id, llm, _Knowledge(kb))


def _system_of(llm: _LLM, call: int = 0) -> str:
    return "\n".join(m["content"] for m in llm.messages[call] if m["role"] == "system")


# ── the happy path ────────────────────────────────────────────────────────────

async def test_a_routine_turn_is_a_single_model_call(db_session) -> None:  # noqa: ANN001
    """v2's worst case was twelve calls on one turn."""
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai kak"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring", pains=["takut telat"],
                            desired_state=["kerja remote"]).to_json())
    llm = _LLM()
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert decision.reply == "halo kak"
    assert len(llm.capabilities) == 1


async def test_a_decisive_turn_is_also_a_single_call_on_the_sales_chain(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(
        db_session,
        dossier=LeadDossier(pains=["takut telat"], desired_state=["kerja remote"],
                            readiness="considering").to_json())
    llm = _LLM()
    decision = await _service(db_session, bid, llm).decide(tid)
    assert decision is not None
    assert llm.capabilities == [SALES]


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

    system = _system_of(llm)
    assert "takut telat" in system
    assert "ALREADY USED" in system and "alumni Dimas" in system


async def test_a_lead_with_only_legacy_needs_still_gets_its_context(db_session) -> None:  # noqa: ANN001
    """The switchover case — a v2 conversation continuing under v3 loses nothing."""
    from app.modules.conversation.needs import NeedsProfile
    bid, tid, _ = await _thread(
        db_session, needs=NeedsProfile(pains=["takut telat"], objections=["mahal"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)

    system = _system_of(llm)
    assert "takut telat" in system and "mahal" in system


async def test_the_cached_prefix_is_the_first_system_message(db_session) -> None:  # noqa: ANN001
    """messages[0] must be exactly the KB surface + contract — per-lead blocks live after it,
    or the broker's prompt cache dies."""
    bid, tid, _ = await _thread(
        db_session, dossier=LeadDossier(pains=["takut telat"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm, kb="KB FACTS UNIQUE").decide(tid)

    first = llm.messages[0][0]
    assert first["role"] == "system"
    assert "KB FACTS UNIQUE" in first["content"]
    assert "takut telat" not in first["content"]  # the dossier is in the variable block


# ── routing ───────────────────────────────────────────────────────────────────

async def test_the_first_llm_turn_runs_on_the_sales_chain(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session)
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)
    assert llm.capabilities[0] == SALES


async def test_a_quiet_mid_conversation_turn_runs_cheap(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai kak"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring", pains=["takut telat"],
                            desired_state=["kerja remote"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)
    assert llm.capabilities == [FAST]


# ── failure handling: a turn is never lost to a contract slip ─────────────────

async def test_a_broken_cheap_answer_escalates_once_to_the_strong_model(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring", pains=["takut telat"],
                            desired_state=["kerja remote"]).to_json())
    llm = _LLM("not json at all", _answer(reply="kembali normal"))
    decision = await _service(db_session, bid, llm).decide(tid)

    assert decision is not None
    assert decision.reply == "kembali normal"
    assert llm.capabilities == [FAST, SMART]


async def test_a_broken_sales_answer_retries_once_on_smart(db_session) -> None:  # noqa: ANN001
    """Degrade to the cheaper chain's quality, never to silence — and stop there."""
    bid, tid, _ = await _thread(db_session, dossier=_DISCOVERED)
    llm = _LLM("not json", "still not json")
    assert await _service(db_session, bid, llm).decide(tid) is None
    assert llm.capabilities == [SALES, SMART]


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
    service = _service(db_session, bid, _LLM(_answer(move="Warm Then Close")))
    await service.decide(tid)
    assert service.last_decision is not None
    assert service.last_decision.move == "warm_then_close"


# ── the money gate: the one check that fails closed ──────────────────────────

_KB_PRICES = "Vibe Coding: harga Rp 13.360.000, DP Rp 500.000."


async def test_an_invented_price_is_rewritten_before_it_reaches_the_lead(db_session) -> None:  # noqa: ANN001
    """A price the school never set is a promise it must honour."""
    bid, tid, _ = await _thread(db_session, texts=(("in", "berapa biayanya kak?"),))
    llm = _LLM(_answer(reply="Investasinya Rp 26.000.000 kak"),
               _answer(reply="Investasinya Rp 13.360.000 kak"))
    decision = await _service(db_session, bid, llm, _KB_PRICES).decide(tid)

    assert decision is not None
    assert "13.360.000" in decision.reply
    assert decision.needs_manager is False


async def test_a_price_that_stays_invented_escalates_rather_than_shipping(db_session) -> None:  # noqa: ANN001
    """The one place the pipeline escalates on its own — and the offending draft is replaced
    by the hold-line, never shipped with only a flag attached."""
    from app.modules.conversation.reply import ESCALATION_HOLD_REPLY

    bid, tid, _ = await _thread(db_session)
    llm = _LLM(_answer(reply="Investasinya Rp 26.000.000 kak"))
    decision = await _service(db_session, bid, llm, _KB_PRICES).decide(tid)

    assert decision is not None
    assert decision.needs_manager is True
    assert decision.reply == ESCALATION_HOLD_REPLY
    assert "базе знаний" in (decision.manager_question or "")


async def test_a_money_rewrite_is_the_turn_ceiling(db_session) -> None:  # noqa: ANN001
    """Generation + one rewrite — never a rewrite chain."""
    bid, tid, _ = await _thread(db_session)
    llm = _LLM(_answer(reply="Investasinya Rp 26.000.000 kak"),
               _answer(reply="Investasinya Rp 13.360.000 kak"))
    await _service(db_session, bid, llm, _KB_PRICES).decide(tid)
    assert len(llm.capabilities) == 2


async def test_a_grounded_price_costs_no_rewrite(db_session) -> None:  # noqa: ANN001
    bid, tid, _ = await _thread(db_session, texts=(("in", "berapa harganya?"),))
    llm = _LLM(_answer(reply="Rp 13.360.000 kak, DP Rp 500.000"))
    decision = await _service(db_session, bid, llm, _KB_PRICES).decide(tid)

    assert decision is not None and "13.360.000" in decision.reply
    assert len(llm.capabilities) == 1


# ── the broker line on the bubble ─────────────────────────────────────────────


async def test_every_bubble_carries_the_broker_line(db_session) -> None:  # noqa: ANN001
    """The chat chip ('🤖 71.2s | #1281991 | free | … | model') is the owner's only view of
    what a reply cost. Between 2026-07-22 and this fix the reply path never recorded the
    meta at all (the v2 engine's `_last_llm_meta = meta` went with it), so 100% of agent
    bubbles on prod rendered a blank chip while follow-ups kept theirs."""
    from sqlmodel import select

    from app.adapters.db.models import Outbox

    bid, tid, _ = await _thread(db_session, dossier=_DISCOVERED)
    service = _service(db_session, bid, _LLM(_answer(reply="satu|||dua")))
    decision = await service.decide(tid)
    assert decision is not None
    await service.enqueue_reply(tid, decision)

    rows = list((await db_session.exec(
        select(Outbox).where(Outbox.thread_id == tid).order_by(Outbox.scheduled_at))).all())
    assert [r.text for r in rows] == ["satu", "dua"]
    assert all(r.llm_info and "fake" in r.llm_info for r in rows)
    assert rows[0].llm_info == rows[1].llm_info


async def test_the_rewrite_is_the_meta_the_bubble_shows(db_session) -> None:  # noqa: ANN001
    """When the money gate rewrites a draft, the shipped text comes from the SECOND call —
    charging the chip with the first call's id would misattribute the turn."""
    bid, tid, _ = await _thread(db_session, dossier=_DISCOVERED)
    llm = _LLM(_answer(reply="Investasinya Rp 26.000.000 kak"),
               _answer(reply="Investasinya Rp 13.360.000 kak"))
    service = _service(db_session, bid, llm, _KB_PRICES)
    await service.decide(tid)
    assert service._last_llm_meta.get("request_id") == "req2"  # noqa: SLF001


def test_a_reply_with_no_broker_call_is_labelled_not_blank() -> None:
    """A templated opener genuinely has no broker line — say so, so the owner can tell
    'no LLM ran' apart from 'the meta was lost'."""
    from app.modules.conversation.engine import TEMPLATED_META, _fmt_llm_meta

    assert _fmt_llm_meta(TEMPLATED_META) == "templated | free"
    assert _fmt_llm_meta({}) is None

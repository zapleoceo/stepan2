"""Discovery cap: a static KB rule alone wasn't reliable (live testing kept seeing a 3rd/4th
discovery question before a direct answer) — decide() now injects a turn-aware nudge the
moment the cap is exceeded, the same mechanism the reply-guard uses for its correction."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlmodel import select

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    KnowledgeDoc,
    Lead,
    Message,
)
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
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000 via transfer BCA atau QRIS."))
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


async def _thread_with_texts(s, texts: list[str]) -> tuple[int, int]:
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=b.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000 via transfer BCA atau QRIS."))
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING)
    s.add_all([ch, lead])
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(th)
    await s.flush()
    for i, txt in enumerate(texts):
        s.add(Message(branch_id=b.id, thread_id=th.id, channel_id=ch.id, external_id=f"m{i}",
                      direction="in", sent_by="lead", text=txt, occurred_at=_NOW))
    await s.flush()
    return b.id, th.id


async def test_ad_opener_only_forces_discovery_not_a_pitch(db_session) -> None:
    """Thread 2983: the lead's only message was the ad's prefilled opener (a button click) and
    the bot pitched the product on turn one. The ad-opener nudge must be injected so it warms
    up + asks a discovery question instead."""
    from app.modules.conversation.situations import AD_OPENER_NUDGE as _AD_OPENER_NUDGE

    bid, tid = await _thread_with_texts(
        db_session, ["💻 Ceritakan lebih detail tentang program kursusnya"])
    llm = _SpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert llm.last_messages[-1]["role"] == "user"
    assert llm.last_messages[-1]["content"] == _AD_OPENER_NUDGE


async def test_no_ad_opener_nudge_once_lead_speaks_own_words(db_session) -> None:
    from app.modules.conversation.situations import AD_OPENER_NUDGE as _AD_OPENER_NUDGE

    bid, tid = await _thread_with_texts(
        db_session, ["💻 Ceritakan lebih detail tentang program kursusnya", "apa itu coding?"])
    llm = _SpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    last = llm.last_messages[-1]
    assert not (last["role"] == "user" and last["content"] == _AD_OPENER_NUDGE)


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


async def test_live_reply_regenerates_a_near_duplicate_question(db_session) -> None:
    """Thread 2260, 2026-07-08: a discovery question re-asked wrapped in new framing slid
    under the followup-only dedup gate because that SECOND occurrence was a live reply, not
    a followup — ReplyService.decide() had no dedup check at all. Must regenerate instead of
    shipping a near-verbatim repeat."""
    bid, tid = await _thread_with_turns(db_session, 1)
    ch_id = (await db_session.exec(select(ChannelThread).where(
        ChannelThread.id == tid))).one().channel_id
    prior_line = "Sebelumnya boleh cerita dikit. Data-nya buat apa Kak?"
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=ch_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior_line, occurred_at=_NOW))
    await db_session.flush()

    class _ScriptLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            self.calls += 1
            reply = ("Oh iya btw, sekalian mau tanya. Data-nya buat apa Kak?"
                     if self.calls == 1 else
                     "Kalau boleh tau, budget-nya kira-kira berapa ya Kak?")
            return json.dumps({"reply": reply, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    llm = _ScriptLLM()
    decision = await ReplyService(
        db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert llm.calls == 2
    assert decision.reply == "Kalau boleh tau, budget-nya kira-kira berapa ya Kak?"


async def test_live_reply_clarifies_if_still_duplicate_after_guard_regen(db_session) -> None:
    """Same precedent as followup.py's post-guard re-check, but a live reply can't just drop
    the send like a nudge can. A repeat is a STYLE dead-end, not a knowledge gap — ask the
    lead to narrow down WITHOUT summoning a manager (threads 2541/2566, false SMM
    escalations); needs_manager stays whatever the model itself decided."""
    from app.modules.conversation import guard

    bid, tid = await _thread_with_turns(db_session, 1)
    ch_id = (await db_session.exec(select(ChannelThread).where(
        ChannelThread.id == tid))).one().channel_id
    prior_line = "Program SMM Intensive ini formatnya hybrid, 3 sesi per minggu online."
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=ch_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior_line, occurred_at=_NOW))
    await db_session.flush()

    class _ScriptLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            self.calls += 1
            if self.calls == 1:
                reply = "Cek promo spesial di https://itstep.id/promo-rahasia ya Kak!"
            else:
                reply = prior_line  # guard's own regen converges back onto the prior line
            return json.dumps({"reply": reply, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    llm = _ScriptLLM()
    decision = await ReplyService(
        db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert decision.reply == guard.CLARIFY_FALLBACK
    assert decision.needs_manager is False


class _MgrLLM:
    """Scripts a needs_manager decision (with a kb_gap so the unexplained-handoff guard
    doesn't fire), to exercise the phone-before-hand-off gate."""

    def __init__(self, reply: str = "tim kami akan bantu ya Kak") -> None:
        self.reply = reply

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return json.dumps({"reply": self.reply, "stage": "presenting",
                           "needs_manager": True, "kb_gap": "lead asked X not in KB"}), \
            {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_needs_manager_without_phone_asks_for_contact_first(db_session) -> None:
    """PHONE BEFORE HAND-OFF: the model wants a manager but the lead has no phone — muting
    the bot would strand a contact-less lead with a manager who can't reach them (lead 2757).
    Suppress the escalation, stay on, and ask for a WhatsApp number first."""
    from app.modules.conversation import guard

    bid, tid = await _thread_with_turns(db_session, 1)
    d = await ReplyService(
        db_session, bid, _MgrLLM(), KnowledgeService(db_session, bid)).decide(tid)
    assert d.needs_manager is False  # not handed off — no contact yet
    assert d.reply == guard.ASK_PHONE_BEFORE_HANDOFF


async def test_needs_manager_with_phone_still_hands_off(db_session) -> None:
    bid, tid = await _thread_with_turns(db_session, 1)
    lead = (await db_session.exec(select(Lead).where(Lead.branch_id == bid))).first()
    lead.phone_e164 = "+628123456789"  # a reachable lead → the hand-off proceeds
    db_session.add(lead)
    await db_session.flush()
    d = await ReplyService(
        db_session, bid, _MgrLLM(), KnowledgeService(db_session, bid)).decide(tid)
    assert d.needs_manager is True


async def test_direct_question_gets_the_answer_not_clarify(db_session) -> None:
    """Sim of thread 2977: the lead asked a CONCRETE question ('Apakah harus modal?') but the
    bot's answer repeated a fact already given, so the near-duplicate guard degraded it to
    'be more specific'. A specific question must get the answer, dup or not."""
    from app.modules.conversation import guard

    bid, tid = await _thread_with_texts(db_session, ["Apakah harus modal?"])
    ch_id = (await db_session.exec(select(ChannelThread).where(
        ChannelThread.id == tid))).one().channel_id
    prior = "Untuk kursusnya memang berbayar ya Kak, Rp 1.670.000 per bulan selama 8 bulan."
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=ch_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior, occurred_at=_NOW))
    await db_session.flush()

    class _DupLLM:
        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            # answers the money question, but near-verbatim to the prior line → dup ≥ 0.6
            return json.dumps({"reply": prior, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    decision = await ReplyService(
        db_session, bid, _DupLLM(), KnowledgeService(db_session, bid)).decide(tid)
    assert decision.reply != guard.CLARIFY_FALLBACK       # not fobbed off with "be specific"
    assert "berbayar" in decision.reply                    # the money answer went through


class _PainThenSpyLLM:
    """First draft catches a pain and pitches; captures the nudge of any redraft."""

    def __init__(self, *, gains: list[str] | None = None) -> None:
        self.calls = 0
        self.nudges: list[str] = []
        self._gains = gains or []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        last = messages[-1]
        if last["role"] == "user" and last["content"].startswith("[System:"):
            self.nudges.append(last["content"])
        return json.dumps({
            "reply": "Investasinya Rp 1.882.955", "stage": "qualifying",
            "pains": ["kurangnya modal"], "gains": self._gains,
        }), {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts, **kw):  # noqa: ANN001, ANN003, ANN201
        return [[0.0] for _ in texts]


async def test_first_pain_without_payoff_triggers_need_payoff_redraft(db_session) -> None:
    """Audit 2026-07-15 (thread 3073): the lead names the pain and the bot answers with the
    price. The nudge alone can't catch that turn — the pain is only in THIS draft, not yet in
    the stored profile — so the reply is redrafted once with the need-payoff instruction."""
    from app.modules.conversation.reply import _NEED_PAYOFF_NUDGE

    bid, tid = await _thread_with_texts(
        db_session, ["mau belajar SMM", "tantangannya kurangnya modal dan ragu untuk memulai"])
    llm = _PainThenSpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert _NEED_PAYOFF_NUDGE in llm.nudges  # the redraft asked for the payoff


async def test_no_redraft_when_the_payoff_is_already_there(db_session) -> None:
    """A pain WITH a gain is a complete need — present away, don't burn an extra call."""
    from app.modules.conversation.reply import _NEED_PAYOFF_NUDGE

    bid, tid = await _thread_with_texts(
        db_session, ["mau belajar SMM", "kurangnya modal tapi pengen punya brand sendiri"])
    llm = _PainThenSpyLLM(gains=["punya brand sendiri"])
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert _NEED_PAYOFF_NUDGE not in llm.nudges


async def test_no_redraft_when_the_lead_asked_a_direct_question(db_session) -> None:
    """They asked — answer-first wins; don't hijack the turn into a discovery question."""
    from app.modules.conversation.reply import _NEED_PAYOFF_NUDGE

    bid, tid = await _thread_with_texts(db_session, ["mau belajar SMM", "berapa harganya kak?"])
    llm = _PainThenSpyLLM()
    await ReplyService(db_session, bid, llm, KnowledgeService(db_session, bid)).decide(tid)
    assert _NEED_PAYOFF_NUDGE not in llm.nudges

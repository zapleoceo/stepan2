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
from app.modules.conversation.routing import SALES, SMART

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
    """What the lead revealed rides into the prompt. What the BOT said does not: stories told
    and arguments made were dropped from the schema on 2026-07-25 — the transcript below the
    dossier already shows them, and restating them cost the model attention for nothing.
    A quoted price is the exception: it is a commitment, not a talking point."""
    bid, tid, _ = await _thread(
        db_session, dossier=LeadDossier(pains=["takut telat"],
                                        prices_quoted=["Rp 13.000.000"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)

    system = _system_of(llm)
    assert "takut telat" in system
    assert "prices you already gave them" in system and "Rp 13.000.000" in system


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


async def test_even_a_quiet_mid_conversation_turn_rides_the_sales_chain(db_session) -> None:  # noqa: ANN001
    """Routing by dossier was removed on 2026-07-25: its conditions read fields populated for
    ~5% of leads, so an engaged lead asking "ini bayar kah kk?" was answered by the cheapest
    model in the chain (thread 4681, numbered menus and brochure language). With the cache warm
    a sales reply costs $0.00057 — there was no saving worth a lost conversation."""
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai kak"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring", pains=["takut telat"],
                            desired_state=["kerja remote"]).to_json())
    llm = _LLM()
    await _service(db_session, bid, llm).decide(tid)
    assert llm.capabilities == [SALES]


# ── failure handling: a turn is never lost to a contract slip ─────────────────

async def test_a_broken_answer_escalates_once_to_the_fallback_chain(db_session) -> None:  # noqa: ANN001
    """Every reply starts on the sales chain; an unparseable body costs one retry on smart,
    never the turn."""
    bid, tid, _ = await _thread(
        db_session, texts=(("in", "halo"), ("out", "hai"), ("in", "oke")),
        dossier=LeadDossier(readiness="exploring", pains=["takut telat"],
                            desired_state=["kerja remote"]).to_json())
    llm = _LLM("not json at all", _answer(reply="kembali normal"))
    decision = await _service(db_session, bid, llm).decide(tid)

    assert decision is not None
    assert decision.reply == "kembali normal"
    assert llm.capabilities == [SALES, SMART]


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


def test_the_dossier_panel_reads_the_field_the_bot_actually_writes() -> None:
    """DossierRepo.save has only ever written `dossier`; the chat panel read `needs`, the v2
    column nothing has written since the cutover. So a manager opening a thread saw an empty
    box while the data sat one column over — thread 5311 had a goal, a pain and a desired
    state recorded and showed none of them."""
    from app.modules.conversation.needs import parse_needs

    v3 = json.dumps({
        "job_to_be_done": "minta penjelasan program Data Analyst",
        "pains": ["belum mengerti"], "desired_state": ["penjelasan simple"],
        "objections": [{"text": "mahal", "status": "open"},
                       {"text": "waktu", "status": "handled"}],
    })
    got = parse_needs(v3)
    assert got.jobs == ["minta penjelasan program Data Analyst"]
    assert got.pains == ["belum mengerti"]
    assert got.gains == ["penjelasan simple"]
    assert got.objections == ["mahal"]  # handled ones are not a manager's problem


def test_the_panel_still_renders_a_legacy_v2_record() -> None:
    """Leads last touched before the cutover keep the old shape — they must not go blank."""
    from app.modules.conversation.needs import parse_needs

    got = parse_needs(json.dumps({"jobs": ["ganti karir"], "pains": ["takut telat"],
                                  "gains": ["kerja remote"], "objections": ["mahal"]}))
    assert got.jobs == ["ganti karir"] and got.gains == ["kerja remote"]


def test_the_pace_of_the_conversation_reaches_the_prompt() -> None:
    """A 20-second reply and a 4-day one look identical in a transcript, and the transcript is
    all the model sees. One is a person sitting in the chat right now; the other has half
    forgotten what was said. Facts only — what to do with them is the model's call."""
    from datetime import datetime as _dt

    from app.modules.conversation.prompt import pace_hint

    now = _dt(2026, 7, 26, 14, 0)
    fast = pace_hint(_dt(2026, 7, 26, 13, 59, 40), _dt(2026, 7, 26, 13, 59, 55), now)
    assert fast is not None and "seconds" in fast
    slow = pace_hint(_dt(2026, 7, 22, 9, 0), _dt(2026, 7, 26, 9, 0), now)
    assert slow is not None and "4 days" in slow
    # Our own lateness is a fact the model cannot otherwise know.
    assert "5 hours" in slow


def test_a_normal_cadence_says_nothing_about_lateness() -> None:
    """Under 15 minutes is the ordinary rhythm of a chat, not a delay worth apologising for."""
    from datetime import datetime as _dt

    from app.modules.conversation.prompt import pace_hint

    now = _dt(2026, 7, 26, 14, 0)
    hint = pace_hint(_dt(2026, 7, 26, 13, 50), _dt(2026, 7, 26, 13, 58), now)
    assert hint is not None
    assert "waiting" not in hint


def test_a_thread_with_no_inbound_has_no_pace() -> None:
    from datetime import datetime as _dt

    from app.modules.conversation.prompt import pace_hint

    assert pace_hint(_dt(2026, 7, 26, 13, 0), None, _dt(2026, 7, 26, 14, 0)) is None


def test_the_selling_model_is_asked_for_the_reply_and_nothing_it_does_not_decide() -> None:
    """Every field the code or the extractor can answer is gone from the sales call.

    stage went because _stage_for overrode it in six branches and the outbox/follow-up/
    reactivation paths owned DORMANT and NURTURING outright; ready went because discovery
    already reports readiness from the lead's own words; product_slug went because it is a
    classification of the transcript, which is the extractor's job. What is left is the reply
    and the one judgement that stops the bot talking."""
    from app.modules.conversation.free_mode import free_contract

    schema = free_contract("id")
    tail = schema[schema.index("Return ONLY this JSON"):]
    assert '"reply"' in tail and '"needs_human"' in tail and '"human_reason"' in tail
    for gone in ('"stage"', '"product_slug"', '"ready"', '"dossier"', '"phone"'):
        assert gone not in tail, gone


def test_the_stage_is_read_off_the_dossier() -> None:
    """The three lines that survived the model no longer being asked for a stage."""
    from app.domain.enums import Stage
    from app.modules.conversation.decision import _stage_from
    from app.modules.conversation.dossier import LeadDossier, Objection

    assert _stage_from(LeadDossier(), ready=True) is Stage.READY
    assert _stage_from(LeadDossier(objections=[Objection("mahal")]),
                       ready=False) is Stage.OBJECTION
    assert _stage_from(LeadDossier(pains=["takut telat"], desired_state=["kerja remote"]),
                       ready=False) is Stage.PRESENTING
    # A captured pain alone is not the emotional layer — keep discovering.
    assert _stage_from(LeadDossier(pains=["takut telat"]), ready=False) is Stage.QUALIFYING
    assert _stage_from(LeadDossier(), ready=False) is Stage.QUALIFYING
    # An open objection outranks a completed discovery: never pitch over a live doubt.
    assert _stage_from(
        LeadDossier(pains=["takut telat"], desired_state=["kerja remote"],
                    objections=[Objection("mahal")]), ready=False) is Stage.OBJECTION


def test_the_output_shape_is_restated_next_to_the_dialogue() -> None:
    """The contract defining the JSON sits in the cached prefix, ~34 000 tokens above the
    lead's newest message, and an instruction that far from the point of generation loses to
    everything nearer — roughly half of Sonnet's answers came back wrapped in a tool-call
    envelope, which is what a model does when it has lost the output shape.

    Twenty tokens restate it where the model can still see it. It must NOT be a second copy of
    the schema (two statements of one contract drift, and the near one then wins the wrong
    argument) and it must NOT touch messages[0], which is the cache anchor."""
    from app.adapters.db.models import Message as _M
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.free_mode import build_messages_free

    dialog = [_M(direction="in", sent_by="lead", text="halo")]
    msgs = build_messages_free("KB", dialog, "id", LeadDossier())
    per_lead = msgs[1]["content"]
    assert per_lead.rstrip().endswith("no markdown fence.]"), "must be the last thing read"
    assert "needs_human" in per_lead
    # A pointer, not a duplicate contract.
    assert "Return ONLY this JSON" not in per_lead
    assert '{{' not in per_lead and '"reply": str' not in per_lead


def test_the_cache_anchor_is_unchanged_by_the_reminder() -> None:
    """messages[0] is byte-identical across leads or the whole prompt cache is worthless —
    a cold Sonnet call costs $0.138 against $0.018 warm (measured 2026-07-26)."""
    from app.adapters.db.models import Message as _M
    from app.modules.conversation.dossier import LeadDossier
    from app.modules.conversation.free_mode import build_messages_free

    a = build_messages_free("KB", [_M(direction="in", sent_by="lead", text="hi")], "id",
                            LeadDossier(pains=["mahal"]))
    b = build_messages_free("KB", [_M(direction="in", sent_by="lead", text="beda")], "id",
                            LeadDossier(role="student"))
    assert a[0]["content"] == b[0]["content"]

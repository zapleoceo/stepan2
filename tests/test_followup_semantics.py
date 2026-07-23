"""S1 follow-up semantics: timer armed by bot sends, counter-based steps, dormant on
exhaustion, inbound resets the cycle, reply-loop watermark (last_out_at), wiring guards."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import select

from app.adapters.db.models import (
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    KnowledgeDoc,
    Lead,
    Message,
    Outbox,
    StageEvent,
)
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.followup import FollowupService
from app.modules.conversation.outbox import OutboxSender
from app.modules.knowledge.service import KnowledgeService
from app.modules.leads.ingest import IngestService
from app.modules.settings.service import _parse, invalidate
from app.ports.channel import InboundMessage, SendResult
from app.worker.wiring import threads_awaiting_reply

_NOW = datetime.now(UTC).replace(tzinfo=None)


def _cfg(**over: str) -> Any:
    raw = {
        "followup_enabled": "true", "quiet_start": "0", "quiet_end": "0",
        "agent_enabled_global": "true", "hourly_cap": "0", "daily_cap": "0",
        **over,
    }
    return _parse(raw)


class FakeLLM:
    def __init__(self, payload: str | None = None) -> None:
        self._payload = payload or json.dumps({"reply": "Halo kak!", "stage": "qualifying"})

    async def chat(self, messages, **kw) -> tuple[str, dict]:  # noqa: ANN001, ANN003
        return self._payload, {"model": "fake", "cost_usd": 0.0}

    async def embed(self, texts):  # noqa: ANN001
        return [[0.0] for _ in texts]


class FakeChannel:
    kind = ChannelKind.INSTAGRAM

    def __init__(self, *, ok: bool = True) -> None:
        self._ok = ok
        self.sent: list[str] = []

    async def fetch_inbound(self):  # noqa: ANN201
        return []

    async def send_text(self, ext_id: str, text: str) -> SendResult:
        self.sent.append(text)
        return SendResult(ok=self._ok, external_message_id="e1" if self._ok else None,
                          error=None if self._ok else "boom")

    async def session_status(self):  # noqa: ANN201
        return None


async def _world(
    s, *, stage: Stage = Stage.QUALIFYING, agent_enabled: bool = True,
    followups_sent: int = 0, timer_due: bool = False, with_dialog: bool = True,
    settings: dict[str, str] | None = None,
) -> tuple[int, int, Lead, ChannelThread]:
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    s.add(KnowledgeDoc(branch_id=branch.id, slug="payment_policy",
        content="Pembayaran: DP Rp 500.000 via BCA/QRIS. Tiket event Rp 100.000."))
    for k, v in (settings or {
        "followup_enabled": "true", "quiet_start": "0", "quiet_end": "0",
        "hourly_cap": "0", "daily_cap": "0",
    }).items():
        s.add(AppSetting(branch_id=branch.id, key=k, value=v))
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=stage, agent_enabled=agent_enabled)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1",
        followups_sent=followups_sent,
        last_out_at=_NOW - timedelta(hours=5),
        last_in_at=_NOW - timedelta(hours=6),
        next_followup_at=_NOW - timedelta(minutes=1) if timer_due else None,
    )
    s.add(thread)
    await s.flush()
    if with_dialog:
        s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                      external_id="m1", direction="in", sent_by="lead", text="halo",
                      occurred_at=_NOW - timedelta(hours=6)))
        await s.flush()
    invalidate(branch.id)
    return branch.id, thread.id, lead, thread


def _svc(s, bid: int, llm: FakeLLM | None = None, cfg=None) -> FollowupService:  # noqa: ANN001
    return FollowupService(s, bid, llm or FakeLLM(), KnowledgeService(s, bid), cfg or _cfg())


async def _pending(s, tid: int) -> Outbox | None:
    q = select(Outbox).where(Outbox.thread_id == tid, Outbox.status == "pending")
    return (await s.exec(q)).first()


# ─── outbox: watermark + arming + dormant ─────────────────────────────────────

async def test_send_sets_last_out_at_and_arms_timer(db_session) -> None:
    bid, tid, _lead, thread = await _world(db_session)
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="hi", source="agent",
                          scheduled_at=_NOW - timedelta(seconds=5)))
    await db_session.flush()
    row = await OutboxSender(db_session, bid, FakeChannel()).send_next(tid)
    assert row is not None and row.status == "sent"
    assert thread.last_out_at is not None and thread.last_out_at >= _NOW
    assert thread.next_followup_at is not None  # armed at sched[0]=4h from send


async def test_send_to_bot_off_lead_does_not_arm_timer(db_session) -> None:
    """After a hard-stop the apology still goes out, but the send must NOT re-arm a nudge —
    agent_enabled=False (or dormant) means no more contact. Anti-ban guard in _plan_followup."""
    bid, tid, _lead, thread = await _world(db_session, agent_enabled=False)
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="sorry", source="agent",
                          scheduled_at=_NOW - timedelta(seconds=5)))
    await db_session.flush()
    row = await OutboxSender(db_session, bid, FakeChannel()).send_next(tid)
    assert row is not None and row.status == "sent"  # apology still delivered
    assert thread.next_followup_at is None  # but no follow-up armed


async def test_manager_send_does_not_touch_followup_cycle(db_session) -> None:
    bid, tid, _lead, thread = await _world(db_session)
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="hi", source="manager",
                          scheduled_at=_NOW - timedelta(seconds=5)))
    await db_session.flush()
    await OutboxSender(db_session, bid, FakeChannel()).send_next(tid)
    assert thread.last_out_at is not None  # watermark still written
    assert thread.next_followup_at is None  # cycle untouched


async def test_followup_send_bumps_step_and_arms_next(db_session) -> None:
    bid, tid, _lead, thread = await _world(db_session, followups_sent=0)
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="nudge", source="followup",
                          scheduled_at=_NOW - timedelta(seconds=5)))
    await db_session.flush()
    await OutboxSender(db_session, bid, FakeChannel()).send_next(tid)
    assert thread.followups_sent == 1  # step counted on the actual send
    assert thread.next_followup_at is not None  # next step armed


async def test_last_followup_send_puts_lead_dormant(db_session) -> None:
    bid, tid, lead, thread = await _world(db_session, followups_sent=3)  # sched exhausted
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="nudge", source="followup",
                          scheduled_at=_NOW - timedelta(seconds=5)))
    await db_session.flush()
    await OutboxSender(db_session, bid, FakeChannel()).send_next(tid)
    assert lead.stage == Stage.DORMANT
    assert thread.next_followup_at is None
    ev = (await db_session.exec(select(StageEvent))).first()
    assert ev is not None and ev.to_stage == str(Stage.DORMANT)


# ─── followup service ─────────────────────────────────────────────────────────

async def test_due_thread_queues_nudge_consumes_timer_without_burning_step(db_session) -> None:
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    assert await _svc(db_session, bid).run() == 1
    row = await _pending(db_session, tid)
    assert row is not None and row.source == "followup" and row.llm_info
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert refreshed.followups_sent == 0  # step burns only on a successful send, not here
    assert refreshed.next_followup_at is None  # timer consumed so run() won't re-queue


class _Notifier:
    def __init__(self) -> None:
        self.sends: list[str] = []

    async def create_topic(self, *, name: str, icon_emoji=None) -> int:  # noqa: ANN001, ARG002
        return 1

    async def send(self, *, text: str, topic_id=None) -> str:  # noqa: ANN001, ARG002
        self.sends.append(text)
        return "ok"


_NM_PAYLOAD = json.dumps({
    "reply": "Untuk yang ini aku cek dulu ke tim ya Kak",
    "stage": "qualifying", "needs_manager": True, "manager_question": "Promo bulan ini?",
    "kb_gap": "no monthly promo info in KB",
})


async def test_followup_needs_manager_no_alert_when_lead_silent(db_session) -> None:
    """The lead is SILENT (that's why we nudge), so manager_question has nothing to quote and
    the model invents one — thread 3072 alerted a schedule question the bot itself raised, 30h
    after the lead's last (non-question) 'halo'. A follow-up needs_manager on a lead whose own
    last message asked nothing must NOT ping a human."""
    from app.adapters.db.models import ManagerAlert

    bid, _tid, lead, _thread = await _world(db_session, timer_due=True)  # last inbound = "halo"
    lead.phone_e164 = "+6281234567890"
    db_session.add(lead)
    await db_session.flush()
    notifier = _Notifier()
    svc = FollowupService(db_session, bid, FakeLLM(_NM_PAYLOAD), KnowledgeService(db_session, bid),
                          _cfg(), notifier=notifier)
    assert await svc.run() == 1                                   # the nudge still goes out
    assert (await db_session.exec(select(ManagerAlert))).first() is None  # but no phantom alert
    assert notifier.sends == []


async def test_followup_drops_nudge_still_duplicate_after_guard_regen(db_session) -> None:
    """Live case (thread 2087, 2026-07-08): the dedup check runs BEFORE guard_decision, so
    it only sees the FIRST draft. If that draft is fresh (passes dedup) but guard flags an
    unrelated fabrication and regenerates, that correction can converge onto text already
    sent — never re-checked, since guard's regen happens after the dedup gate. Re-check the
    FINAL text post-guard and drop the send rather than repeat something already said."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    prior_line = "Program SMM Intensive ini formatnya hybrid, 3 sesi per minggu online."
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior_line, occurred_at=_NOW - timedelta(hours=1)))
    await db_session.flush()

    class _ScriptLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            self.calls += 1
            if self.calls == 1:
                # fresh draft (passes the pre-guard dedup check) but with a fabricated URL
                reply = "Cek promo spesial di https://itstep.id/promo-rahasia ya Kak!"
            else:
                # guard's OWN regen (for the fabrication) converges back onto the prior line
                reply = prior_line
            return json.dumps({"reply": reply, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    llm = _ScriptLLM()
    assert await _svc(db_session, bid, llm=llm).run() == 0  # dropped, not sent
    assert await _pending(db_session, tid) is None
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    # Dry step BURNED: leaving the timer due meant this thread regenerated (and dropped) a
    # nudge every 10-min tick — the schedule advances instead, next attempt hours away.
    assert refreshed.followups_sent == 1
    assert refreshed.next_followup_at is not None
    assert refreshed.next_followup_at > _NOW + timedelta(hours=3)  # step 2 = +4h, not +10min


async def test_followup_nudge_goes_through_the_reply_guard(db_session) -> None:
    """Followup nudges used to bypass the reply guard entirely (only ReplyService.decide
    called it) — a fabricated link in a nudge would ship unblocked. Must be caught the same
    way a live reply's fabrication is."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    db_session.add(AppSetting(branch_id=bid, key="reply_guard", value="urls"))
    invalidate(bid)
    fake_link = "https://lab.itstep.id/cybersecurity-practice?access=HANDAYANI2024"
    payload = json.dumps({"reply": f"cek di {fake_link} ya", "stage": "qualifying"})
    # The guard catches the link, the regen (same fake LLM) can't fix it, and the resulting
    # canned SAFE_FALLBACK is NOT worth sending as an unsolicited nudge - the whole send is
    # dropped and the schedule step burned (thread 1230: "I'll check with the team" fired
    # into a 14-day silence with no question asked).
    assert await _svc(db_session, bid, llm=FakeLLM(payload)).run() == 0
    row = await _pending(db_session, tid)
    assert row is None  # nothing queued at all - never the fabricated link, never the stub
    await db_session.refresh(thread)
    assert thread.followups_sent >= 1  # the step was burned, not left due to loop forever


async def test_followup_splits_bubbles_like_a_normal_reply(db_session) -> None:
    """A nudge that comes back with the '|||' bubble marker must be split into separate
    outbox rows, same as enqueue_reply — otherwise the raw marker leaks into the sent
    text (observed live: chat 1778 got 'Kak rkhaa... ||| Banyak lho...' as one message)."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    payload = json.dumps({"reply": "Bubble one ||| Bubble two", "stage": "qualifying"})
    assert await _svc(db_session, bid, llm=FakeLLM(payload)).run() == 1
    rows = (await db_session.exec(
        select(Outbox).where(Outbox.thread_id == tid).order_by(Outbox.id))).all()
    assert [r.text for r in rows] == ["Bubble one", "Bubble two"]
    assert all("|||" not in r.text for r in rows)
    assert rows[1].scheduled_at > rows[0].scheduled_at  # staggered like a normal reply


async def test_followup_queues_during_quiet_hours(db_session) -> None:
    """Quiet hours hold the SEND (OutboxSender.send_next), not the queue — a nudge armed
    at 23:50 must already be sitting in outbox, ready the instant quiet hours lift."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    always_quiet = _cfg(quiet_start="0", quiet_end="24")
    assert always_quiet.is_quiet_hour() is True
    assert await _svc(db_session, bid, cfg=always_quiet).run() == 1
    row = await _pending(db_session, tid)
    assert row is not None and row.source == "followup"


async def test_lead_spoke_last_not_followed_up(db_session) -> None:
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    thread.last_in_at = _NOW - timedelta(minutes=10)  # newer than last_out
    await db_session.flush()
    assert await _svc(db_session, bid).run() == 0


async def test_pending_outbox_blocks_followup(db_session) -> None:
    bid, tid, _, _ = await _world(db_session, timer_due=True)
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="queued", source="agent"))
    await db_session.flush()
    assert await _svc(db_session, bid).run() == 0


async def test_new_stage_excluded_from_followups(db_session) -> None:
    bid, _, _, _ = await _world(db_session, stage=Stage.NEW, timer_due=True)
    assert await _svc(db_session, bid).run() == 0


async def test_bad_llm_json_does_not_burn_attempt_or_crash(db_session) -> None:
    bid, tid, _, thread = await _world(db_session, timer_due=True)
    assert await _svc(db_session, bid, llm=FakeLLM("not json at all")).run() == 0
    assert thread.followups_sent == 0
    assert await _pending(db_session, tid) is None


async def test_global_agent_off_stops_followups(db_session) -> None:
    bid, _, _, _ = await _world(db_session, timer_due=True)
    cfg = _cfg(agent_enabled_global="false")
    assert await _svc(db_session, bid, cfg=cfg).run() == 0


# ─── ingest resets + revive ───────────────────────────────────────────────────

def _inbound(text: str = "halo lagi") -> InboundMessage:
    return InboundMessage(external_thread_id="ig-1", sender_id="u1", text=text,
                          occurred_at=_NOW)


async def test_fresh_inbound_resets_cycle_and_skips_queued_nudge(db_session) -> None:
    bid, tid, _, thread = await _world(db_session, followups_sent=2, timer_due=True)
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="nudge", source="followup"))
    await db_session.flush()
    await IngestService(db_session, bid).ingest(thread.channel_id, [_inbound()])
    assert thread.followups_sent == 0 and thread.next_followup_at is None
    q = select(Outbox).where(Outbox.thread_id == tid, Outbox.source == "followup")
    assert (await db_session.exec(q)).first().status == "skipped"


async def test_inbound_revives_dormant_to_qualifying(db_session) -> None:
    bid, _, lead, thread = await _world(db_session, stage=Stage.DORMANT, agent_enabled=False)
    await IngestService(db_session, bid).ingest(thread.channel_id, [_inbound()])
    assert lead.stage == Stage.QUALIFYING and lead.agent_enabled is True
    ev = (await db_session.exec(select(StageEvent))).first()
    assert ev is not None and ev.actor == "system"


async def test_inbound_does_not_reenable_bot_on_human_led_stage(db_session) -> None:
    bid, _, lead, thread = await _world(db_session, stage=Stage.MANAGER, agent_enabled=False)
    await IngestService(db_session, bid).ingest(thread.channel_id, [_inbound()])
    assert lead.agent_enabled is False and lead.stage == Stage.MANAGER


# ─── wiring guards ────────────────────────────────────────────────────────────

async def test_awaiting_reply_respects_agent_toggle_and_pending(db_session) -> None:
    bid, tid, lead, thread = await _world(db_session)
    thread.last_in_at = _NOW  # lead spoke last
    thread.last_out_at = _NOW - timedelta(hours=1)
    await db_session.flush()
    assert tid in await threads_awaiting_reply(db_session, bid)

    lead.agent_enabled = False
    await db_session.flush()
    assert tid not in await threads_awaiting_reply(db_session, bid)

    lead.agent_enabled = True
    db_session.add(Outbox(branch_id=bid, thread_id=tid, text="queued", source="agent"))
    await db_session.flush()
    assert tid not in await threads_awaiting_reply(db_session, bid)


async def test_failed_generation_backs_off_instead_of_immediate_retry(db_session) -> None:
    """Cost leak (2026-07-22): a failed queue_one (broker error/timeout) left next_followup_at
    untouched, so the exact same thread got re-picked and re-billed on the very next 10-min
    cron tick, over and over, during a broker-instability window (763 followup broker calls
    that day for only 196 sent messages). A failure must push the timer forward instead."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)

    class _RaisingLLM(FakeLLM):
        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            raise RuntimeError("broker 502")

    svc = _svc(db_session, bid, llm=_RaisingLLM())
    ok = await svc.queue_one(tid, None, 0)
    assert ok is False
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert refreshed.next_followup_at is not None
    assert refreshed.next_followup_at > _NOW + timedelta(minutes=10)  # not due next tick


# ── v3 nudges: driven by the dossier, and recording what they learn ───────────

class _CapturingLLM(FakeLLM):
    """Remembers the prompts it was handed and which tier each call ran on."""

    def __init__(self, payload: str | None = None) -> None:
        super().__init__(payload)
        self.messages: list[list[dict]] = []
        self.capabilities: list[str] = []

    async def chat(self, messages, **kw) -> tuple[str, dict]:  # noqa: ANN001, ANN003
        self.messages.append(messages)
        self.capabilities.append(kw.get("capability", ""))
        return await super().chat(messages, **kw)


def _v3(reply: str = "Eh iya kak, btw batch berikutnya mulai bulan depan", **over) -> str:  # noqa: ANN003
    payload = {"reply": reply, "move": "give_value", "stage": "qualifying"}
    payload.update(over)
    return json.dumps(payload)


async def _dossier_of(s, bid: int, lead_id: int):  # noqa: ANN001, ANN202
    from app.modules.conversation.repository import DossierRepo
    return await DossierRepo(s, bid).load(lead_id)


async def _set_dossier(s, lead: Lead, dossier) -> None:  # noqa: ANN001
    lead.dossier = dossier.to_json()
    s.add(lead)
    await s.flush()


async def test_a_lead_who_refused_outright_gets_no_further_nudges(db_session) -> None:  # noqa: ANN001
    """v2 needed a regex over the last message to notice; the dossier remembers it, so it
    survives the lead never repeating themselves."""
    from app.modules.conversation.dossier import LeadDossier

    bid, tid, lead, thread = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(refusal="blunt"))
    llm = _CapturingLLM(_v3())

    assert await _svc(db_session, bid, llm).run() == 0
    assert llm.capabilities == []                       # not even generated
    assert await _pending(db_session, tid) is None
    assert (await db_session.get(ChannelThread, tid)).next_followup_at is None  # timer cancelled


async def test_a_softly_refused_lead_still_gets_a_gentler_touch(db_session) -> None:  # noqa: ANN001
    from app.modules.conversation.dossier import LeadDossier

    bid, tid, lead, _ = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(refusal="soft"))
    llm = _CapturingLLM(_v3())

    assert await _svc(db_session, bid, llm).run() == 1
    framing = llm.messages[0][-1]["content"]
    assert "do NOT argue" in framing


async def test_the_nudge_is_told_not_to_beg(db_session) -> None:  # noqa: ANN001
    bid, _tid, _lead, _ = await _world(db_session, timer_due=True)
    llm = _CapturingLLM(_v3())
    await _svc(db_session, bid, llm).run()

    framing = llm.messages[0][-1]["content"]
    assert "masih minat?" in framing and "begging" in framing


async def test_what_the_lead_already_heard_reaches_the_nudge_prompt(db_session) -> None:  # noqa: ANN001
    """Repetition is prevented by telling the model what it already used, not by diffing text."""
    from app.modules.conversation.dossier import LeadDossier

    bid, _tid, lead, _ = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(
        pains=["takut telat mulai"], cases_used=["alumni Dimas"]))
    llm = _CapturingLLM(_v3())
    await _svc(db_session, bid, llm).run()

    system = llm.messages[0][0]["content"]
    assert "takut telat mulai" in system
    assert "ALREADY USED" in system and "alumni Dimas" in system


async def test_a_nudge_records_what_it_learned(db_session) -> None:  # noqa: ANN001
    """The v2 leak this closes: follow-ups never wrote back a word, so an objection uncovered
    by a nudge was thrown away."""
    from app.modules.conversation.dossier import LeadDossier

    bid, _tid, lead, _ = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(pains=["takut telat mulai"]))
    llm = _CapturingLLM(_v3(dossier={"objections": [{"text": "mahal", "status": "open"}]}))

    assert await _svc(db_session, bid, llm).run() == 1
    stored = await _dossier_of(db_session, bid, lead.id)
    assert stored.open_objections() == ["mahal"]
    assert stored.pains == ["takut telat mulai"]        # nothing already known was lost


async def test_an_open_objection_buys_the_strong_model(db_session) -> None:  # noqa: ANN001
    from app.modules.conversation.dossier import LeadDossier, Objection

    bid, _tid, lead, _ = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(
        objections=[Objection("mahal")], pains=["takut telat"],
        desired_state=["kerja remote"]))
    llm = _CapturingLLM(_v3())
    await _svc(db_session, bid, llm).run()
    assert llm.capabilities == ["chat:smart"]


async def test_an_ordinary_nudge_runs_cheap(db_session) -> None:  # noqa: ANN001
    from app.modules.conversation.dossier import LeadDossier

    bid, _tid, lead, _ = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(
        pains=["takut telat"], desired_state=["kerja remote"]))
    llm = _CapturingLLM(_v3())
    await _svc(db_session, bid, llm).run()
    assert llm.capabilities == ["chat:fast"]


async def test_nothing_new_to_say_burns_the_step_instead_of_sending(db_session) -> None:  # noqa: ANN001
    """A thread with nothing left to say used to regenerate a dropped nudge every tick — the
    single biggest token sink measured."""
    bid, tid, _lead, _ = await _world(db_session, timer_due=True)

    assert await _svc(db_session, bid, _CapturingLLM(_v3(reply="  "))).run() == 0
    assert await _pending(db_session, tid) is None
    thread = await db_session.get(ChannelThread, tid)
    assert thread.next_followup_at is None or thread.next_followup_at > _NOW


async def test_a_nudge_quoting_a_price_that_is_not_in_the_kb_is_dropped(db_session) -> None:  # noqa: ANN001
    """Nobody asked, so there is nothing to escalate — just don't send a wrong number."""
    bid, tid, _lead, _ = await _world(db_session, timer_due=True)
    llm = _CapturingLLM(_v3(reply="Promo khusus kak, cuma Rp 26.000.000 aja!"))

    assert await _svc(db_session, bid, llm).run() == 0
    assert await _pending(db_session, tid) is None


async def test_a_grounded_price_is_still_uninvited_and_gets_dropped(db_session) -> None:  # noqa: ANN001
    """Thread 4849: a nudge volunteered the full price and instalments — nobody had asked, and
    the figure being real (in the KB) didn't make it any less unprompted. Being grounded only
    used to save it from the ungrounded-money check; it still needed an invitation."""
    bid, tid, _lead, _ = await _world(db_session, timer_due=True)
    llm = _CapturingLLM(_v3(reply="DP-nya Rp 500.000 aja kak, bisa dicicil"))

    assert await _svc(db_session, bid, llm).run() == 0
    assert await _pending(db_session, tid) is None


async def test_a_price_nudge_is_fine_once_the_lead_is_ready(db_session) -> None:  # noqa: ANN001
    """A ready lead who's gone quiet can still be nudged with the number they already agreed
    to move on — that's a close, not a volunteered pitch."""
    from app.modules.conversation.dossier import LeadDossier

    bid, tid, lead, _ = await _world(db_session, timer_due=True)
    await _set_dossier(db_session, lead, LeadDossier(readiness="ready"))
    llm = _CapturingLLM(_v3(reply="DP-nya Rp 500.000 aja kak, bisa dicicil"))

    assert await _svc(db_session, bid, llm).run() == 1
    row = await _pending(db_session, tid)
    assert row is not None and "500.000" in row.text

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


async def test_followup_needs_manager_alerts_on_a_real_question(db_session) -> None:
    """When the lead's OWN last message really does ask something, the follow-up needs_manager
    still alerts — the genuine case this path was built for."""
    from app.adapters.db.models import ManagerAlert, Message

    bid, tid, lead, thread = await _world(db_session, timer_due=True)
    lead.phone_e164 = "+6281234567890"
    db_session.add(lead)
    # the lead's last message is a real question (still older than last_out → timer stays due)
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="q1", direction="in", sent_by="lead",
                           text="ada diskon ga kak?", occurred_at=_NOW - timedelta(hours=5)))
    await db_session.flush()
    notifier = _Notifier()
    svc = FollowupService(db_session, bid, FakeLLM(_NM_PAYLOAD), KnowledgeService(db_session, bid),
                          _cfg(), notifier=notifier)
    assert await svc.run() == 1
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "needs_manager"
    assert alert.lead_phone == "+6281234567890"
    assert len(notifier.sends) == 1


_AD_PREFILL = "Halo, saya ingin tahu detail program SMM dan biaya kursusnya 😊"


async def test_followup_needs_manager_no_alert_on_ad_template_question(db_session) -> None:
    """Thread 3926: the ad button's canned text contains 'biaya', so the question-gate alone
    read it as the lead asking a price — and raised a phantom 'Berapa biaya?' alert for a lead
    who never typed a word. The button's text is the ad's, never the lead's."""
    from app.adapters.db.models import ManagerAlert, Message

    bid, tid, lead, thread = await _world(db_session, timer_due=True)
    lead.phone_e164 = "+6281234567890"
    db_session.add(lead)
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="ad1", direction="in", sent_by="lead",
                           text=_AD_PREFILL, occurred_at=_NOW - timedelta(hours=5)))
    await db_session.flush()
    notifier = _Notifier()
    svc = FollowupService(db_session, bid, FakeLLM(_NM_PAYLOAD), KnowledgeService(db_session, bid),
                          _cfg(), notifier=notifier)
    assert await svc.run() == 1                                   # nudge still goes out
    assert (await db_session.exec(select(ManagerAlert))).first() is None
    assert notifier.sends == []


async def test_followup_to_silent_clicker_carries_no_price_constraint(db_session) -> None:
    """A lead whose ONLY message is the ad button must not get a price in a follow-up either
    (thread 3926: the first-ever follow-up dumped 'Rp 1.882.955 — DP 500.000'). The nudge to
    the model must carry the silent-clicker constraint block."""
    from app.adapters.db.models import Message
    from app.modules.conversation.situations import FOLLOWUP_SILENT_CLICKER_EXTRA

    bid, tid, _lead, _thread = await _world(db_session, timer_due=True)
    # make the ONLY inbound the ad prefill — the lead never spoke their own words
    msg = (await db_session.exec(select(Message).where(Message.thread_id == tid,
                                                       Message.direction == "in"))).first()
    msg.text = _AD_PREFILL
    db_session.add(msg)
    await db_session.flush()

    captured: list[str] = []

    class _CaptureLLM(FakeLLM):
        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            captured.append(messages[-1]["content"] if messages else "")
            return await super().chat(messages, **kw)

    svc = FollowupService(db_session, bid, _CaptureLLM(), KnowledgeService(db_session, bid),
                          _cfg(), notifier=None)
    assert await svc.run() == 1
    assert any(FOLLOWUP_SILENT_CLICKER_EXTRA.strip() in c for c in captured), \
        "silent-clicker follow-up must carry the no-price constraint"


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


async def test_dry_drop_on_last_step_winds_down_to_dormant(db_session) -> None:
    """A dry drop on the LAST schedule step exhausts the cycle exactly like a sent last
    nudge would: timer cleared, lead → dormant, bot off (no due-forever regeneration)."""
    bid, tid, lead, thread = await _world(db_session, timer_due=True, followups_sent=3)
    prior_line = "Program SMM Intensive ini formatnya hybrid, 3 sesi per minggu online."
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior_line, occurred_at=_NOW - timedelta(hours=1)))
    await db_session.flush()
    # the draft itself is a near-duplicate → regen (smart) converges onto the same line
    llm = FakeLLM(json.dumps({"reply": prior_line, "stage": "qualifying"}))
    assert await _svc(db_session, bid, llm=llm).run() == 0
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert refreshed.next_followup_at is None  # exhausted — no more attempts
    assert lead.stage == Stage.DORMANT and lead.agent_enabled is False
    ev = (await db_session.exec(select(StageEvent).where(
        StageEvent.to_stage == str(Stage.DORMANT)))).first()
    assert ev is not None and "dry" in ev.reason


async def test_followup_regenerates_a_near_duplicate_nudge(db_session) -> None:
    """Chat 1830: the 2nd follow-up re-greeted the lead and re-asked the exact same
    discovery question already sent — must regenerate on the strong model instead of
    shipping a near-verbatim repeat."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True, followups_sent=1)
    prior_line = ("Halo Kak! Seneng banget Kakak tertarik dengan SMM. Boleh cerita dulu, "
                 "Kakak pengen belajar SMM untuk apa ya?")
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior_line, occurred_at=_NOW - timedelta(hours=5)))
    await db_session.flush()

    class _ScriptLLM:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            self.calls += 1
            reply = prior_line if self.calls == 1 else \
                "Btw, kalau budget masih jadi kendala, ada Skill Booster lebih ringan lho 😊"
            return json.dumps({"reply": reply, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    llm = _ScriptLLM()
    assert await _svc(db_session, bid, llm=llm).run() == 1
    assert llm.calls == 2  # first draft (near-duplicate) + one regen
    row = await _pending(db_session, tid)
    assert row is not None and "Skill Booster" in row.text


async def test_followup_dedup_regen_prompt_anchors_to_the_leads_last_message(
    db_session,
) -> None:
    """Thread 2085: the regen instruction only said 'pick a different angle', with no
    anchor to what the lead actually said — the model answered with a bare 'Mantap, Kak!'
    and the lead replied 'Mantap apa nya kak?' (great about what?). The correction prompt
    sent to the model must carry the lead's own last message forward."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True, followups_sent=1)
    prior_line = "Halo Kak! Seneng banget Kakak tertarik. Boleh cerita dulu, mau belajar apa?"
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="out1", direction="out", sent_by="agent",
                           text=prior_line, occurred_at=_NOW - timedelta(hours=5)))
    await db_session.flush()

    class _SpyLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.regen_prompt: str | None = None

        async def chat(self, messages, **kw):  # noqa: ANN001, ANN003
            self.calls += 1
            if self.calls == 1:
                reply = prior_line
            else:
                self.regen_prompt = messages[-1]["content"]
                reply = "Boleh, langsung aku kirim silabusnya ya"
            return json.dumps({"reply": reply, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    llm = _SpyLLM()
    assert await _svc(db_session, bid, llm=llm).run() == 1
    assert llm.regen_prompt is not None
    assert "halo" in llm.regen_prompt.lower()  # the lead's own last message ("halo")


async def test_followup_regenerates_when_only_one_bubble_of_several_is_a_duplicate(
    db_session,
) -> None:
    """Thread 237: a 3-bubble followup opened with a bubble byte-for-byte identical to an
    earlier live reply ("Untuk Sabtu/Minggu kantor kami memang tutup Kak..."), but the two
    EXTRA bubbles that followed diluted the whole-message ratio well under the dedup gate.
    Must catch a duplicate in ANY one bubble, not just the message as a whole."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    prior_line = "Untuk Sabtu/Minggu kantor kami memang tutup Kak, jadi kunjungan belum bisa 🙏"
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
                reply = (f"{prior_line}|||Tapi ada Demo Event Sabtu 18 Juli jam 9 pagi|||"
                         "Tiketnya cuma Rp 100.000 aja")
            else:
                reply = "Kalau mau, Demo Event Sabtu 18 Juli masih ada slot - mau aku catat?"
            return json.dumps({"reply": reply, "stage": "qualifying"}), \
                {"model": "fake", "cost_usd": 0.0}

        async def embed(self, texts):  # noqa: ANN001
            return [[0.0] for _ in texts]

    llm = _ScriptLLM()
    assert await _svc(db_session, bid, llm=llm, cfg=_cfg(reply_guard="urls")).run() == 1
    assert llm.calls == 2  # first draft (duplicate bubble) + one regen
    row = await _pending(db_session, tid)
    assert row is not None and prior_line not in row.text


async def test_followup_nudge_goes_through_the_reply_guard(db_session) -> None:
    """Followup nudges used to bypass the reply guard entirely (only ReplyService.decide
    called it) — a fabricated link in a nudge would ship unblocked. Must be caught the same
    way a live reply's fabrication is."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    db_session.add(AppSetting(branch_id=bid, key="reply_guard", value="urls"))
    invalidate(bid)
    fake_link = "https://lab.itstep.id/cybersecurity-practice?access=HANDAYANI2024"
    payload = json.dumps({"reply": f"cek di {fake_link} ya", "stage": "qualifying"})
    assert await _svc(db_session, bid, llm=FakeLLM(payload)).run() == 1
    row = await _pending(db_session, tid)
    assert row is not None and fake_link not in row.text  # guard scrubbed the fabricated link


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


async def test_followup_skipped_when_lead_signaled_annoyance(db_session) -> None:
    """Threads 2045/1996: the lead showed clear irritation ('Sok asik banget', 'Gak usah
    ganggu aku lagi') in a live reply, but the next SCHEDULED follow-up fired anyway and
    re-pitched the same price, ignoring the signal entirely. A follow-up is proactive (the
    lead didn't ask for it) and must never fire on top of an unaddressed annoyance signal."""
    bid, tid, _lead, thread = await _world(db_session, timer_due=True)
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=thread.channel_id,
                           external_id="in2", direction="in", sent_by="lead",
                           text="Gak usah ganggu aku lagi",
                           occurred_at=_NOW - timedelta(minutes=5)))
    await db_session.flush()
    llm = FakeLLM()
    assert await _svc(db_session, bid, llm=llm).run() == 0
    assert await _pending(db_session, tid) is None
    refreshed = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    # timer CANCELLED, not merely skipped — a skipped-but-due thread was re-picked (and its
    # annoyance re-checked) every 10-min tick forever; an annoyed lead gets no more nudges
    assert refreshed.next_followup_at is None


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

"""Stage application in the reply pipeline (S1 semantics): decision → lead.stage +
stage_event journal, hand-off (agent off, alert, CAPI), budget gate in decide()."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import select

from app.adapters.db.models import (
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    Lead,
    ManagerAlert,
    Message,
    Outbox,
    Product,
    StageEvent,
)
from app.adapters.meta_capi import MetaCapi
from app.domain.enums import BOT_SILENT_STAGES, ChannelKind, Stage
from app.modules.conversation import ReplyService
from app.modules.conversation.decision import Decision
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import _parse, invalidate

_NOW = datetime.now(UTC).replace(tzinfo=None)


class FakeLLM:
    def __init__(self, decision: dict[str, Any] | None = None) -> None:
        self._payload = json.dumps(decision or {"reply": "ok", "stage": "qualifying"})

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        return self._payload, {"model": "fake", "cost_usd": 0.02}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


class FakeNotifier:
    def __init__(self) -> None:
        self.sends: list[str] = []

    async def create_topic(self, *, name: str, icon_emoji=None) -> int:  # noqa: ANN001, ARG002
        return 1

    async def send(self, *, text: str, topic_id=None) -> str:  # noqa: ANN001, ARG002
        self.sends.append(text)
        return "ok"


async def _world(s, *, phone: str | None = None, stage: Stage = Stage.NEW,
                 settings: dict[str, str] | None = None,
                 inbounds: list[str] | None = None) -> tuple[int, int, Lead]:
    branch = Branch(name="T", lang="id")
    s.add(branch)
    await s.flush()
    for k, v in (settings or {}).items():
        s.add(AppSetting(branch_id=branch.id, key=k, value=v))
    ch = Channel(branch_id=branch.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=branch.id, stage=stage, phone_e164=phone)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(thread)
    await s.flush()
    for i, txt in enumerate(inbounds or ["halo"]):
        s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                      external_id=f"m{i}", direction="in", sent_by="lead", text=txt,
                      occurred_at=_NOW))
    await s.flush()
    invalidate(branch.id)
    return branch.id, thread.id, lead


class CapRecordingLLM:
    """Returns broken JSON on chat:fast, a valid decision on chat:smart; records the tiers
    it was asked for — to prove a broken cheap decision escalates to the strong model."""
    def __init__(self) -> None:
        self.caps: list[str] = []

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        cap = kw.get("capability")
        self.caps.append(cap)
        if cap == "chat:fast":
            return "{ this is not valid json", {"model": "fast", "cost_usd": 0.0}
        return json.dumps({"reply": "ok", "stage": "qualifying"}), {"model": "smart",
                                                                    "cost_usd": 0.02}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        return [[0.0] for _ in texts]


def _decision(**over: Any) -> Decision:
    base: dict[str, Any] = {
        "reply": "ok", "stage": Stage.QUALIFYING, "product_slug": None,
        "ready": False, "needs_manager": False,
    }
    base.update(over)
    return Decision(**base)


def _svc(s, bid: int, notifier=None, llm=None) -> ReplyService:  # noqa: ANN001
    return ReplyService(s, bid, llm or FakeLLM(), KnowledgeService(s, bid),
                        branch_settings=_parse({}), notifier=notifier)


async def test_enqueue_reply_drops_duplicate_when_already_answered_meanwhile(db_session) -> None:
    """Idempotency backstop (2026-07-07): if a sibling run already sent a reply to this exact
    inbound while THIS run was slow (a broker call near its own timeout ceiling, or a killed+
    retried worker job), last_out_at already caught up to last_in_at by the time we reach
    enqueue_reply — drop the duplicate instead of sending a second reply."""
    bid, tid, _lead = await _world(db_session)
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    thread.last_in_at = _NOW
    thread.last_out_at = _NOW  # a sibling already answered this inbound
    db_session.add(thread)
    await db_session.flush()
    out = await _svc(db_session, bid).enqueue_reply(tid, _decision())
    assert out is None
    assert (await db_session.exec(select(StageEvent))).first() is None  # no stage churn either


async def test_stage_applied_with_journal(db_session) -> None:
    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision())
    assert lead.stage == Stage.QUALIFYING
    ev = (await db_session.exec(select(StageEvent))).first()
    assert ev is not None and ev.from_stage == "new" and ev.to_stage == "qualifying"
    assert ev.actor == "bot"


async def test_bot_stage_change_logs_its_own_reason_to_thread_log(db_session) -> None:
    """The model's own explanation for a funnel move is logged the same way a manual
    stage-move's reason popup is — visible in the chat chronology, not just internal."""
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(
        tid, _decision(stage_reason="лид назвал конкретную боль — переход в presenting"))
    assert lead.stage == Stage.QUALIFYING
    log = (await db_session.exec(select(ThreadLog))).first()
    assert log is not None and log.kind == "stage_reason"
    assert log.detail == "лид назвал конкретную боль — переход в presenting"
    assert log.actor == "bot"


async def test_no_thread_log_row_when_stage_reason_is_absent(db_session) -> None:
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision())  # no stage_reason
    assert lead.stage == Stage.QUALIFYING
    assert (await db_session.exec(select(ThreadLog))).first() is None


async def test_no_thread_log_row_when_stage_does_not_change(db_session) -> None:
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session, stage=Stage.QUALIFYING)
    await _svc(db_session, bid).enqueue_reply(
        tid, _decision(stage_reason="should not be logged — no move"))
    assert lead.stage == Stage.QUALIFYING
    assert (await db_session.exec(select(ThreadLog))).first() is None


async def test_forced_manager_override_logs_kb_gap_not_the_models_own_stage_reason(
    db_session,
) -> None:
    """Threads 1520/1995: needs_manager forces the stage to MANAGER regardless of what the
    model itself requested, but the model's stage_reason describes ITS OWN requested stage
    (e.g. 'presenting') - logging it as-is next to a presenting→manager row reads as a
    mismatch. kb_gap actually explains the escalation and must win."""
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(
        needs_manager=True, kb_gap="лид спросил про рассрочку 24 месяца — нет в базе",
        stage_reason="лид уточнил детали — переход в presenting"))
    assert lead.stage == Stage.MANAGER
    log = (await db_session.exec(select(ThreadLog))).first()
    assert log is not None and log.detail == "лид спросил про рассрочку 24 месяца — нет в базе"


async def test_forced_manager_override_falls_back_when_kb_gap_is_missing(db_session) -> None:
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(
        needs_manager=True, manager_question="ada trial class gratis?"))
    assert lead.stage == Stage.MANAGER
    log = (await db_session.exec(select(ThreadLog))).first()
    assert log is not None and log.detail == "ada trial class gratis?"


async def test_forced_manager_override_never_leaves_the_chronology_blank(db_session) -> None:
    """Threads 2390/2392/2403: needs_manager fired with the model leaving stage_reason,
    kb_gap AND manager_question all null - the chronology must still show something rather
    than a silent gap."""
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(needs_manager=True))
    assert lead.stage == Stage.MANAGER
    log = (await db_session.exec(select(ThreadLog))).first()
    assert log is not None and log.detail


async def test_forced_manager_override_never_logs_the_models_stage_reason(db_session) -> None:
    """A guard-forced hand-off (thread 2541): kb_gap/manager_question empty but the model set
    a stage_reason for a DIFFERENT stage. That reason must NOT leak into the MANAGER row —
    the generic fallback is logged instead (the guard itself stamps kb_gap in production)."""
    from app.adapters.db.models import ThreadLog

    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(
        needs_manager=True, stage_reason="лид назвал цель — переход в presenting"))
    assert lead.stage == Stage.MANAGER
    log = (await db_session.exec(select(ThreadLog))).first()
    assert log is not None and "presenting" not in log.detail


async def test_same_stage_writes_no_event(db_session) -> None:
    bid, tid, _ = await _world(db_session, stage=Stage.QUALIFYING)
    await _svc(db_session, bid).enqueue_reply(tid, _decision())
    assert (await db_session.exec(select(StageEvent))).first() is None


async def test_ready_with_phone_hands_off(db_session, monkeypatch) -> None:
    capi_calls: list[str] = []

    async def fake_post(self, pixel_id, token, payload):  # noqa: ANN001, ANN201
        capi_calls.append(payload["data"][0]["event_id"])
        return True

    monkeypatch.setattr(MetaCapi, "_post", fake_post)
    notifier = FakeNotifier()
    bid, tid, lead = await _world(
        db_session, phone="+6281234567890",
        settings={"meta_pixel_id": "pix", "meta_capi_token": "tok"},
    )
    svc = ReplyService(db_session, bid, FakeLLM(), KnowledgeService(db_session, bid),
                       branch_settings=_parse({"meta_pixel_id": "pix",
                                               "meta_capi_token": "tok"}),
                       notifier=notifier)
    await svc.enqueue_reply(tid, _decision(ready=True, stage=Stage.PRESENTING))
    assert lead.stage == Stage.READY
    assert lead.agent_enabled is False
    assert lead.handed_off_at is not None
    assert lead.ready_subtype == "deal"
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_deal"
    assert alert.lead_phone == "+6281234567890"
    assert len(notifier.sends) == 1  # one group ping for the hand-off
    assert capi_calls == [f"handoff-{bid}-{lead.id}"]


async def test_ready_without_phone_keeps_selling(db_session) -> None:
    bid, tid, lead = await _world(db_session, phone=None)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(ready=True))
    assert lead.stage == Stage.PRESENTING
    assert lead.agent_enabled is True


async def test_ready_handoff_appends_a_closing_line_for_the_lead(db_session) -> None:
    """The won/READY exit muted the bot but never guaranteed the lead a 'what happens next'
    line (unlike the manager exit). The fresh READY flip now appends _READY_HANDOFF_CLOSING."""
    from app.modules.conversation.delivery import _READY_HANDOFF_CLOSING

    bid, tid, _lead = await _world(db_session, phone="+6281234567890")
    await _svc(db_session, bid).enqueue_reply(tid, _decision(ready=True, stage=Stage.PRESENTING))
    rows = (await db_session.exec(
        select(Outbox).where(Outbox.thread_id == tid).order_by(Outbox.scheduled_at))).all()
    assert [r.text for r in rows] == ["ok", _READY_HANDOFF_CLOSING]  # model's reply, then it


async def test_repeated_non_target_winds_down_to_dormant(db_session) -> None:
    """A lead already classified non_target on a prior turn and STILL non_target now gets its
    closing line, then exits to DORMANT (bot off) — no more lingering in the active queue."""
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.QUALIFYING)
    lead.lead_type = "non_target"  # already classified off-topic on an earlier turn
    db_session.add(lead)
    await db_session.flush()
    await _svc(db_session, bid).enqueue_reply(
        tid, _decision(lead_type="non_target", reply="Baik Kak, semoga sukses ya 🙏"))
    assert lead.stage == Stage.DORMANT and lead.agent_enabled is False
    ev = (await db_session.exec(select(StageEvent).where(
        StageEvent.to_stage == str(Stage.DORMANT)))).first()
    assert ev is not None and ev.reason == "non_target"
    # the polite closing the model wrote still went out before the wind-down
    rows = (await db_session.exec(select(Outbox).where(Outbox.thread_id == tid))).all()
    assert any("semoga sukses" in r.text for r in rows)


async def test_first_non_target_classification_does_not_dormant_yet(db_session) -> None:
    """The FIRST time a lead is classified non_target it still gets one normal turn — only a
    REPEAT (was non_target coming in AND still is) winds down, mirroring _NON_TARGET_NUDGE."""
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.QUALIFYING)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(lead_type="non_target"))
    assert lead.stage != Stage.DORMANT  # not wound down on the first classification


def test_to_e164_canonicalizes_a_typed_phone() -> None:
    from app.modules.leads.phone import to_e164
    assert to_e164("0812 3456 7890", "62") == "+6281234567890"   # ID local trunk → +62
    assert to_e164("+62 812-3456-7890") == "+6281234567890"      # explicit international kept
    assert to_e164("0123-456-789", "60") == "+60123456789"       # MY branch stamps +60, not +62
    assert to_e164("0917 123 4567", "63") == "+639171234567"     # PH branch → +63
    assert to_e164("call me") is None                            # no digits
    assert to_e164("123") is None                                # too short to be a number


async def test_stage_ready_without_phone_is_gated(db_session) -> None:
    # The model put stage='ready' directly (not the flag) with no phone — must NOT hand off
    # (the lead-1561 defect). Gate keeps it selling until a contact exists.
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.PRESENTING)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(ready=False, stage=Stage.READY))
    assert lead.stage == Stage.PRESENTING
    assert lead.agent_enabled is True
    assert lead.handed_off_at is None


async def test_phone_captured_from_decision_enables_handoff(db_session) -> None:
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.PRESENTING)
    # Lead typed their WA number this turn: decision.phone is captured → phone gate now passes.
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(ready=True, stage=Stage.PRESENTING, phone="0812 3456 7890"),
    )
    assert lead.phone_e164 == "+6281234567890"  # local 08… normalised to +62…
    assert lead.stage == Stage.READY
    assert lead.agent_enabled is False


async def test_bot_never_moves_a_lead_out_of_manager_stage(db_session) -> None:
    """Live bug (thread 2274): a manager manually moved a lead to MANAGER; the bot's very
    next decision moved it straight back to qualifying on its own read of the chat, silently
    overriding the manager's call. Only a manual UI action may move a lead OUT of a human-led
    stage — the bot can keep talking (agent_enabled untouched here) but never touches stage."""
    bid, tid, lead = await _world(db_session, stage=Stage.MANAGER)
    lead.agent_enabled = True  # manager kept the bot ON while still holding the lead
    db_session.add(lead)
    await db_session.flush()
    await _svc(db_session, bid).enqueue_reply(tid, _decision(stage=Stage.QUALIFYING))
    assert lead.stage == Stage.MANAGER  # unchanged despite the model's own stage read
    assert lead.agent_enabled is True  # bot may still reply — this rule is stage-only
    assert (await db_session.exec(select(StageEvent))).first() is None  # no phantom transition


async def test_bot_never_moves_a_lead_out_of_ready_stage(db_session) -> None:
    bid, tid, lead = await _world(db_session, stage=Stage.READY, phone="+6281234567890")
    await _svc(db_session, bid).enqueue_reply(
        tid, _decision(stage=Stage.PRESENTING, needs_manager=True))
    assert lead.stage == Stage.READY


async def test_needs_manager_moves_to_manager_and_mutes(db_session) -> None:
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone="+6281234567890")
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(needs_manager=True, manager_question="Промокод?"),
    )
    assert lead.stage == Stage.MANAGER
    assert lead.agent_enabled is False
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "needs_manager"
    assert alert.lead_phone == "+6281234567890"


async def test_needs_manager_appends_a_closing_line_for_the_lead(db_session) -> None:
    """Live case (thread 1023): needs_manager mutes the bot, but nothing told the LEAD a
    human was taking over — a follow-up they sent days later got pure silence. The bot's own
    turn must include a closing bubble saying a human will follow up."""
    from app.modules.conversation.delivery import _MANAGER_HANDOFF_CLOSING

    bid, tid, _lead = await _world(db_session, phone="+6281234567890")
    out = await _svc(db_session, bid).enqueue_reply(
        tid, _decision(needs_manager=True, manager_question="Jadwal Demo Event?"),
    )
    assert out is not None and out.text == _MANAGER_HANDOFF_CLOSING
    rows = (await db_session.exec(
        select(Outbox).where(Outbox.thread_id == tid).order_by(Outbox.scheduled_at))).all()
    assert [r.text for r in rows] == ["ok", _MANAGER_HANDOFF_CLOSING]  # model's reply, then it


async def test_no_closing_line_when_already_in_manager_stage(db_session) -> None:
    """Don't re-append the closing line on every subsequent needs_manager turn once the
    lead is already muted — only the turn that FLIPS the stage gets it."""
    from app.modules.conversation.delivery import _MANAGER_HANDOFF_CLOSING

    bid, tid, _lead = await _world(db_session, phone="+6281234567890", stage=Stage.MANAGER)
    await _svc(db_session, bid).enqueue_reply(
        tid, _decision(needs_manager=True, manager_question="Another question?"),
    )
    rows = (await db_session.exec(select(Outbox).where(Outbox.thread_id == tid))).all()
    assert all(r.text != _MANAGER_HANDOFF_CLOSING for r in rows)


async def test_openhouse_rsvp_notifies_without_muting_bot(db_session) -> None:
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone="+6281234567890", stage=Stage.PRESENTING)
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(ready=True, ready_subtype="openhouse", stage=Stage.PRESENTING),
    )
    assert lead.stage == Stage.PRESENTING  # no forced stage jump — bot keeps talking
    assert lead.agent_enabled is True
    assert lead.ready_subtype == "openhouse"
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_openhouse"
    assert alert.lead_phone == "+6281234567890"
    assert len(notifier.sends) == 1
    assert "09:00-18:00" in notifier.sends[0] or "09:00-18:00" in alert.summary_en


async def test_openhouse_rsvp_without_phone_does_not_notify(db_session) -> None:
    """Policy: no team ping for a contact-less RSVP — the bot keeps talking and collects the
    WhatsApp first; the ping fires only once a phone is in hand (mirrors the manager gate)."""
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.QUALIFYING)
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(ready=True, ready_subtype="openhouse", stage=Stage.QUALIFYING),
    )
    assert lead.stage == Stage.QUALIFYING and lead.agent_enabled is True  # bot keeps selling
    assert (await db_session.exec(select(ManagerAlert))).first() is None  # no contact-less ping
    assert lead.ready_subtype != "openhouse"  # not marked notified → can ping later w/ a phone
    assert notifier.sends == []


async def test_openhouse_rsvp_ignores_conflicting_needs_manager_flag(db_session) -> None:
    """The model sometimes also flags needs_manager on an RSVP turn — the openhouse
    notification already covers the hand-off, so it must not ALSO flip to MANAGER."""
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone="+6281234567890", stage=Stage.PRESENTING)
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid,
        _decision(ready=True, ready_subtype="openhouse", needs_manager=True,
                  stage=Stage.PRESENTING),
    )
    assert lead.stage == Stage.PRESENTING
    assert lead.agent_enabled is True
    alerts = (await db_session.exec(select(ManagerAlert))).all()
    assert len(alerts) == 1  # only the openhouse alert, no duplicate generic one
    assert alerts[0].kind == "ready_openhouse"


async def test_openhouse_with_model_written_ready_stage_stays_present(db_session) -> None:
    """Defensive depth: even if the model illegally writes stage='ready' on an openhouse
    RSVP turn, the notify-only channel must NOT mute the bot — remap READY→PRESENTING."""
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone="+6281234567890", stage=Stage.PRESENTING)
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(ready=True, ready_subtype="openhouse", stage=Stage.READY),
    )
    assert lead.stage == Stage.PRESENTING  # not READY — bot keeps talking
    assert lead.agent_enabled is True
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_openhouse"


async def test_event_product_forces_openhouse_rsvp(db_session) -> None:
    """When the bound product is kind='event', a 'ready' is an RSVP regardless of the model's
    subtype guess: notify-only, bot stays on — never a course-deal hand-off."""
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone="+6281234567890", stage=Stage.PRESENTING)
    db_session.add(Product(branch_id=bid, slug="vc_demo", title="Demo Event", kind="event"))
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    thread.product_slug = "vc_demo"
    db_session.add(thread)
    await db_session.flush()
    # the model treated it as a course 'deal' — the event kind must override to openhouse
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(ready=True, ready_subtype="deal", stage=Stage.PRESENTING,
                       product_slug="vc_demo"),
    )
    assert lead.stage == Stage.PRESENTING     # event = notify-only, no READY hand-off
    assert lead.agent_enabled is True         # bot keeps talking
    assert lead.ready_subtype == "openhouse"
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_openhouse"


async def test_manager_stage_not_in_bot_silent() -> None:
    assert Stage.MANAGER not in BOT_SILENT_STAGES  # silence is per-lead agent_enabled


async def test_product_slug_attributed_once(db_session) -> None:
    bid, tid, _ = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(product_slug="vibe"))
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert thread.product_slug == "vibe"


async def test_hard_stop_dormant_bot_off_timer_cleared(db_session) -> None:
    bid, tid, lead = await _world(db_session, stage=Stage.QUALIFYING)
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    thread.next_followup_at = _NOW  # a pending nudge that must be revoked
    db_session.add(thread)
    await db_session.flush()
    out = await _svc(db_session, bid).enqueue_reply(tid, _decision(hard_stop=True))
    assert out is not None  # the one apology still queues and goes out
    assert lead.stage == Stage.DORMANT
    assert lead.agent_enabled is False
    thread = (await db_session.exec(
        select(ChannelThread).where(ChannelThread.id == tid))).first()
    assert thread.next_followup_at is None
    ev = (await db_session.exec(
        select(StageEvent).where(StageEvent.reason == "hard_stop"))).first()
    assert ev is not None and ev.to_stage == "dormant" and ev.actor == "bot"


async def test_hard_stop_when_already_dormant_writes_no_duplicate_event(db_session) -> None:
    bid, tid, lead = await _world(db_session, stage=Stage.DORMANT)
    await _svc(db_session, bid).enqueue_reply(tid, _decision(hard_stop=True))
    assert lead.agent_enabled is False
    assert (await db_session.exec(select(StageEvent))).first() is None


async def test_budget_gate_blocks_decide(db_session) -> None:
    bid, tid, _ = await _world(db_session, settings={"daily_budget_usd": "0.01"})
    svc = _svc(db_session, bid)
    first = await svc.decide(tid)  # records 0.02 → over budget after this call
    assert first is not None
    assert await svc.decide(tid) is None  # gated now


# ─── soft-no snooze: no kill, no nudge storm (audit of threads >=2000) ───

async def _thread_of(s, tid: int) -> ChannelThread:  # noqa: ANN001
    return (await s.exec(select(ChannelThread).where(ChannelThread.id == tid))).one()


async def _set_last_inbound(s, tid: int, text: str) -> None:  # noqa: ANN001
    msg = (await s.exec(select(Message).where(Message.thread_id == tid,
                                              Message.direction == "in"))).first()
    msg.text = text
    s.add(msg)
    await s.flush()


async def test_soft_no_lands_in_objection_not_dormant(db_session) -> None:
    """Threads 2275/2493/2689: the model set DORMANT the moment a lead said "next time aja"
    — dead on the spot with zero follow-ups. A polite 'not now' is an objection to work
    later, not a corpse."""
    bid, tid, lead = await _world(db_session, stage=Stage.QUALIFYING)
    await _set_last_inbound(db_session, tid, "Nggak kak, makasih. Next time aja ya")
    thread = await _thread_of(db_session, tid)

    await _svc(db_session, bid)._apply_decision(
        lead, thread, _decision(stage=Stage.DORMANT, reply="Baik Kak, kabari ya"))
    assert lead.stage == Stage.OBJECTION


async def test_soft_no_collapses_the_cycle_to_one_final_nudge(db_session) -> None:
    """The naive fix (just forbid DORMANT) would drop the lead into the normal 1/4/24/120h
    cycle — FOUR nudges after they said no, the ban vector from threads 2045/1996. Exactly
    one dated re-contact must remain."""
    bid, tid, lead = await _world(db_session, stage=Stage.QUALIFYING)
    await _set_last_inbound(db_session, tid, "Nanti saya fikirkan lagi ya kak")
    thread = await _thread_of(db_session, tid)
    svc = _svc(db_session, bid)

    assert await svc._snooze_on_soft_no(lead, thread) is True
    schedule = _parse({}).followup_schedule_h
    assert thread.followups_sent == len(schedule) - 1  # only the last, longest step is left


async def test_annoyed_lead_is_never_snoozed_or_re_contacted(db_session) -> None:
    """"Stop bothering me" belongs to the hard-stop path — a snooze would re-contact someone
    who explicitly told us to go away (the exact escalation in threads 2045/1996)."""
    bid, tid, lead = await _world(db_session, stage=Stage.QUALIFYING)
    await _set_last_inbound(db_session, tid, "gak usah ganggu aku lagi")
    thread = await _thread_of(db_session, tid)

    assert await _svc(db_session, bid)._snooze_on_soft_no(lead, thread) is False
    assert thread.followups_sent == 0  # nothing planned


async def test_engaged_lead_is_not_snoozed(db_session) -> None:
    """The snooze must not fire on a normal, interested reply — that would silently cut a
    live conversation down to a single nudge."""
    bid, tid, lead = await _world(db_session, stage=Stage.QUALIFYING)
    await _set_last_inbound(db_session, tid, "boleh kak, saya tertarik banget")
    thread = await _thread_of(db_session, tid)

    assert await _svc(db_session, bid)._snooze_on_soft_no(lead, thread) is False
    assert thread.followups_sent == 0

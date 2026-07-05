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
                 settings: dict[str, str] | None = None) -> tuple[int, int, Lead]:
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
    s.add(Message(branch_id=branch.id, thread_id=thread.id, channel_id=ch.id,
                  external_id="m1", direction="in", sent_by="lead", text="halo",
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


async def test_fast_broken_json_escalates_to_smart(db_session) -> None:
    bid, tid, _ = await _world(db_session, stage=Stage.NEW)  # early + no signal → routes to fast
    llm = CapRecordingLLM()
    decision = await _svc(db_session, bid, llm=llm).decide(tid)
    assert llm.caps == ["chat:fast", "chat:smart"]  # tried cheap, escalated on broken JSON
    assert decision is not None and decision.reply == "ok"


def _svc(s, bid: int, notifier=None, llm=None) -> ReplyService:  # noqa: ANN001
    return ReplyService(s, bid, llm or FakeLLM(), KnowledgeService(s, bid),
                        branch_settings=_parse({}), notifier=notifier)


async def test_stage_applied_with_journal(db_session) -> None:
    bid, tid, lead = await _world(db_session)
    await _svc(db_session, bid).enqueue_reply(tid, _decision())
    assert lead.stage == Stage.QUALIFYING
    ev = (await db_session.exec(select(StageEvent))).first()
    assert ev is not None and ev.from_stage == "new" and ev.to_stage == "qualifying"
    assert ev.actor == "bot"


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


def test_normalize_phone() -> None:
    from app.modules.conversation.reply import _normalize_phone
    assert _normalize_phone("0812 3456 7890") == "+6281234567890"   # ID local trunk → +62
    assert _normalize_phone("+62 812-3456-7890") == "+6281234567890"
    assert _normalize_phone("81234567890") == "+81234567890"        # bare digits keep as-is
    assert _normalize_phone("call me") is None                      # no digits
    assert _normalize_phone("123") is None                          # too short to be a number


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


async def test_openhouse_rsvp_without_phone_still_notifies(db_session) -> None:
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.QUALIFYING)
    await _svc(db_session, bid, notifier=notifier).enqueue_reply(
        tid, _decision(ready=True, ready_subtype="openhouse", stage=Stage.QUALIFYING),
    )
    assert lead.stage == Stage.QUALIFYING
    assert lead.agent_enabled is True
    alert = (await db_session.exec(select(ManagerAlert))).first()
    assert alert is not None and alert.kind == "ready_openhouse"


async def test_openhouse_rsvp_ignores_conflicting_needs_manager_flag(db_session) -> None:
    """The model sometimes also flags needs_manager on an RSVP turn — the openhouse
    notification already covers the hand-off, so it must not ALSO flip to MANAGER."""
    notifier = FakeNotifier()
    bid, tid, lead = await _world(db_session, phone=None, stage=Stage.PRESENTING)
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

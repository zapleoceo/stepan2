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

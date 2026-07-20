"""SLA re-ping: an unworked ready alert nudges the manager once, tagged; a manager reply or a
second pass does not re-ping again."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.adapters.db.models import (
    Branch,
    Channel,
    ChannelThread,
    Lead,
    ManagerAlert,
    Message,
)
from app.config import settings
from app.domain.enums import ChannelKind, Stage
from app.modules.notifications.escalation import EscalationService, _within_hours

_NOW = datetime.now(UTC).replace(tzinfo=None)


class _FakeNotifier:
    def __init__(self) -> None:
        self.sends: list[dict] = []
        self.topics_created = 0

    async def create_topic(self, *, name: str, icon_emoji=None) -> int:  # noqa: ANN001, ARG002
        self.topics_created += 1
        return 99

    async def send(self, *, text: str, topic_id=None) -> str:  # noqa: ANN001
        self.sends.append({"text": text, "topic_id": topic_id})
        return "ok"


def test_within_hours_window() -> None:
    assert _within_hours(9, "8-21") and _within_hours(20, "8-21")
    assert not _within_hours(7, "8-21") and not _within_hours(21, "8-21")
    assert _within_hours(3, "bad-window")  # malformed never blocks


async def _ready_alert(s, *, age_min: int = 10, phone: str = "+628123", kind="ready_deal",
                       topic=7):
    b = Branch(name="T", lang="id", tz_offset_h=7)
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    s.add(ch)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=Stage.READY, phone_e164=phone, notify_topic_id=topic)
    s.add(lead)
    await s.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    s.add(th)
    await s.flush()
    a = ManagerAlert(branch_id=b.id, lead_id=lead.id, thread_id=th.id, kind=kind,
                     created_at=_NOW - timedelta(minutes=age_min))
    s.add(a)
    await s.flush()
    return b.id, th.id, a, ch.id


@pytest.fixture(autouse=True)
def _wide_window(monkeypatch):
    monkeypatch.setattr(settings(), "reping_hours_wib", "0-24")
    monkeypatch.setattr(settings(), "manager_tag", "@citraasiha")
    monkeypatch.setattr(settings(), "alert_reping_after_min", 5)
    monkeypatch.setattr(settings(), "alert_reping_max_age_min", 180)


async def test_stale_ready_alert_repings_manager_once(db_session) -> None:
    bid, _tid, alert, _cid = await _ready_alert(db_session)
    notifier = _FakeNotifier()
    sent = await EscalationService(db_session, bid, notifier).run()
    assert sent == 1
    assert len(notifier.sends) == 1 and "@citraasiha" in notifier.sends[0]["text"]
    assert notifier.sends[0]["topic_id"] == 7  # into the lead's own topic, never General
    await db_session.refresh(alert)
    assert alert.reping_at is not None
    # a second pass must NOT re-ping (reping_at already set)
    assert await EscalationService(db_session, bid, notifier).run() == 0
    assert len(notifier.sends) == 1


async def test_fresh_alert_within_sla_not_repinged(db_session) -> None:
    bid, _tid, _a, _cid = await _ready_alert(db_session, age_min=2)
    notifier = _FakeNotifier()
    assert await EscalationService(db_session, bid, notifier).run() == 0
    assert notifier.sends == []


async def test_stale_backlog_alert_past_ceiling_not_repinged(db_session) -> None:
    # 2026-07-20 incident: the first run re-pinged alerts up to 16 days old. An alert older
    # than alert_reping_max_age_min (180) must NEVER re-ping — the moment has passed.
    bid, _tid, _a, _cid = await _ready_alert(db_session, age_min=16 * 24 * 60)
    notifier = _FakeNotifier()
    assert await EscalationService(db_session, bid, notifier).run() == 0
    assert notifier.sends == []


async def test_reping_creates_topic_when_missing_never_general(db_session) -> None:
    # the 2026-07-20 flood went to the group's General because old leads had no topic; a
    # re-ping must open the lead's own topic (like the alert) and send there, never General.
    bid, _tid, _a, _cid = await _ready_alert(db_session, topic=None)
    notifier = _FakeNotifier()
    assert await EscalationService(db_session, bid, notifier).run() == 1
    assert notifier.topics_created == 1
    assert notifier.sends[0]["topic_id"] == 99  # the freshly created topic, not None (General)


async def test_manager_reply_suppresses_reping(db_session) -> None:
    bid, tid, alert, cid = await _ready_alert(db_session)
    db_session.add(Message(branch_id=bid, thread_id=tid, channel_id=cid, external_id="mgr-1",
                           direction="out", sent_by="manager", text="halo Kak, saya bantu ya",
                           occurred_at=_NOW - timedelta(minutes=1)))
    await db_session.flush()
    notifier = _FakeNotifier()
    assert await EscalationService(db_session, bid, notifier).run() == 0
    assert notifier.sends == []


async def test_needs_manager_without_phone_not_repinged(db_session) -> None:
    bid, _tid, _a, _cid = await _ready_alert(db_session, phone=None, kind="needs_manager")
    notifier = _FakeNotifier()
    assert await EscalationService(db_session, bid, notifier).run() == 0

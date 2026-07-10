"""sending_enabled must be a hard stop on the actual send, independent of the bot on/off
toggle (agent_enabled) — the lever for 'account got soft-blocked, keep capturing incoming
and queueing replies, but don't touch the channel until I say so'.

It is now a PER-CONNECTOR setting enforced inside OutboxSender.send_next (so one channel
can be paused while another keeps sending), and the platform kill switch is enforced in the
send_outbox dispatcher's fan-out gate."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.adapters.db.models import AppSetting, Branch, Channel, ChannelThread, Lead, Outbox
from app.domain.enums import ChannelKind
from app.modules.conversation.outbox import OutboxSender
from app.modules.settings.service import invalidate
from app.ports.channel import SendResult
from app.worker import main as worker_main
from app.worker import wiring


class _FakeChannel:
    kind = ChannelKind.INSTAGRAM

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def fetch_inbound(self) -> list[Any]:
        return []

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        self.sent.append((external_thread_id, text))
        return SendResult(ok=True, external_message_id="x")

    async def session_status(self) -> Any:
        return None


async def test_sending_disabled_holds_the_send(db_session) -> None:
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()
    db_session.add(AppSetting(branch_id=b.id, key="sending_enabled", value="false"))
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    db_session.add(ch)
    lead = Lead(branch_id=b.id)
    db_session.add(lead)
    await db_session.flush()
    thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1")
    db_session.add(thread)
    await db_session.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(Outbox(branch_id=b.id, thread_id=thread.id, text="hi", source="agent",
                          status="pending", scheduled_at=now - timedelta(seconds=5)))
    await db_session.flush()
    invalidate(b.id)

    channel = _FakeChannel()
    row = await OutboxSender(db_session, b.id, channel).send_next(thread.id)

    assert row is None            # nothing sent while the connector is paused
    assert channel.sent == []
    # the queued row is still pending — ready for when sending resumes
    assert (await db_session.get(Outbox, 1)).status == "pending"


async def test_send_outbox_halts_when_platform_kill_switch_off(monkeypatch) -> None:
    """The emergency platform switch must stop the REAL IG writes, not just generation — with
    it OFF, the dispatcher fans out nothing (and doesn't even enumerate branches)."""
    reached = {"branches": False}

    async def _platform_off(_session):
        return False

    async def _fake_active_branches(_session):
        reached["branches"] = True
        return []

    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_off)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)

    attempted = await worker_main.send_outbox({"redis": object()})
    assert attempted == 0
    assert reached["branches"] is False  # short-circuited before touching any branch

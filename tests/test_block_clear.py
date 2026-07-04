"""Block chat (is_blocked) + clear-history (context_cleared_at): worker skips blocked
leads, ingest won't revive a blocked lead's bot, dialog drops pre-clear history."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, Message
from app.api._ui_html import chat_block_pill_html
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.repository import MessageRepo
from app.modules.leads.ingest import IngestService
from app.ports.channel import InboundMessage
from app.worker.wiring import threads_awaiting_reply

_NOW = datetime.now(UTC).replace(tzinfo=None)


async def _thread(s, *, blocked: bool = False, cleared=None):
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM)
    lead = Lead(branch_id=b.id, stage=Stage.QUALIFYING, agent_enabled=not blocked,
                is_blocked=blocked)
    s.add(ch)
    s.add(lead)
    await s.flush()
    thread = ChannelThread(
        lead_id=lead.id, channel_id=ch.id, external_thread_id="ig-1",
        last_in_at=_NOW, last_out_at=_NOW - timedelta(hours=1), context_cleared_at=cleared,
    )
    s.add(thread)
    await s.flush()
    return b.id, ch.id, lead, thread


async def test_blocked_lead_not_awaiting_reply(db_session) -> None:
    bid, _cid, lead, thread = await _thread(db_session, blocked=True)
    assert thread.id not in await threads_awaiting_reply(db_session, bid)
    lead.is_blocked = False
    lead.agent_enabled = True  # unblock + re-enable → now eligible
    await db_session.flush()
    assert thread.id in await threads_awaiting_reply(db_session, bid)


async def test_ancient_backlog_thread_not_awaiting_reply(db_session) -> None:
    """REGRESSION: flipping agent_enabled_global back on for a branch that had been
    sync-only for months must not mass-reply to a year-old backlog out of nowhere (real
    incident: threads with last_in_at back to 2025-08-26 all got auto-replied to at once
    the moment the branch was turned on). Anything past the recency window needs a
    deliberate, reviewed catch-up, not an automatic one."""
    bid, _cid, _lead, thread = await _thread(db_session)
    thread.last_in_at = _NOW - timedelta(days=10)
    thread.last_out_at = None
    await db_session.flush()
    assert thread.id not in await threads_awaiting_reply(db_session, bid)


async def test_ingest_does_not_revive_blocked_lead(db_session) -> None:
    bid, cid, lead, thread = await _thread(db_session, blocked=True)
    await IngestService(db_session, bid).ingest(cid, [InboundMessage(
        external_thread_id="ig-1", sender_id="u1", text="halo", occurred_at=_NOW,
    )])
    assert lead.agent_enabled is False and lead.is_blocked is True  # still muted


async def test_dialog_drops_pre_clear_history(db_session) -> None:
    bid, cid, _lead, thread = await _thread(db_session)
    for i, mins in enumerate((30, 20, 5)):  # 30m & 20m ago are "before clear", 5m after
        db_session.add(Message(
            branch_id=bid, thread_id=thread.id, channel_id=cid, external_id=f"m{i}",
            direction="in", sent_by="lead", text=f"msg{i}",
            occurred_at=_NOW - timedelta(minutes=mins),
        ))
    await db_session.flush()
    cleared = _NOW - timedelta(minutes=10)
    dialog = await MessageRepo(db_session, bid).dialog(thread.id, since=cleared)
    assert [m.text for m in dialog] == ["msg2"]  # only the post-clear message
    full = await MessageRepo(db_session, bid).dialog(thread.id)
    assert len(full) == 3  # without the watermark, all three


def test_block_pill_renders_state() -> None:
    from app.api._i18n import _lang
    _lang.set("en")
    assert "/ui/chat/7/block" in chat_block_pill_html(7, False)
    assert "blocked" in chat_block_pill_html(7, True)

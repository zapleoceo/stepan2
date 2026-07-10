"""Connector-scope settings: the three-tier resolver (platform → branch → connector), the
per-connector follow-up cadence, and the per-channel anti-ban cap count."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import UTC, datetime, timedelta  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    AppSetting,
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Outbox,
)
from app.domain.enums import ChannelKind  # noqa: E402
from app.modules.settings.repository import SettingRepo  # noqa: E402
from app.modules.settings.service import (  # noqa: E402
    get_channel_settings,
    get_settings,
    invalidate,
)


async def _branch(s, **kw) -> Branch:
    b = Branch(name="B", lang="id", **kw)
    s.add(b)
    await s.flush()
    return b


async def _channel(s, branch_id, kind=ChannelKind.INSTAGRAM) -> Channel:
    c = Channel(branch_id=branch_id, kind=kind)
    s.add(c)
    await s.flush()
    return c


async def test_three_tier_precedence(db_session) -> None:
    """channel overrides branch overrides platform, key by key."""
    b = await _branch(db_session)
    c = await _channel(db_session, b.id)
    db_session.add(AppSetting(branch_id=None, channel_id=None, key="hourly_cap", value="10"))
    db_session.add(AppSetting(branch_id=b.id, channel_id=None, key="hourly_cap", value="20"))
    db_session.add(AppSetting(branch_id=b.id, channel_id=c.id, key="hourly_cap", value="30"))
    # daily_cap set only at branch level → the channel view inherits it
    db_session.add(AppSetting(branch_id=b.id, channel_id=None, key="daily_cap", value="99"))
    await db_session.flush()

    branch_view = await SettingRepo(db_session).load_all(b.id)
    channel_view = await SettingRepo(db_session).load_all(b.id, c.id)

    assert branch_view["hourly_cap"] == "20"     # branch wins over platform
    assert channel_view["hourly_cap"] == "30"    # connector wins over branch
    assert channel_view["daily_cap"] == "99"     # inherited from branch tier


async def test_get_channel_settings_overrides_only_the_channel(db_session) -> None:
    b = await _branch(db_session)
    c = await _channel(db_session, b.id)
    db_session.add(AppSetting(branch_id=b.id, channel_id=None,
                              key="followup_schedule_h", value="1,4,24,120"))
    db_session.add(AppSetting(branch_id=b.id, channel_id=c.id,
                              key="followup_schedule_h", value="1,4,12"))
    await db_session.flush()
    invalidate(b.id)

    branch_cfg = await get_settings(db_session, b.id)
    channel_cfg = await get_channel_settings(db_session, b.id, c.id)

    assert branch_cfg.followup_schedule_h == [1, 4, 24, 120]   # branch default
    assert channel_cfg.followup_schedule_h == [1, 4, 12]       # this connector's override


async def test_invalidate_branch_flushes_channel_views(db_session) -> None:
    """A branch-level write must drop every cached (branch, channel) view too, since channel
    resolutions fall back to the branch tier."""
    b = await _branch(db_session)
    c = await _channel(db_session, b.id)
    db_session.add(AppSetting(branch_id=b.id, channel_id=None, key="daily_cap", value="5"))
    await db_session.flush()
    invalidate(b.id)
    assert (await get_channel_settings(db_session, b.id, c.id)).daily_cap == 5  # cache it

    # change the branch value, invalidate the BRANCH (not the channel key) — the channel view
    # must reflect the new fallback, proving invalidate(branch) cleared its (branch, channel) entry.
    await SettingRepo(db_session).upsert("daily_cap", "7", branch_id=b.id)
    invalidate(b.id)
    assert (await get_channel_settings(db_session, b.id, c.id)).daily_cap == 7


async def test_anti_ban_cap_counts_per_channel(db_session) -> None:
    """A cap is counted against ONLY the channel's own sends — one connector's volume never
    consumes another's budget."""
    from app.modules.conversation.repository import OutboxRepo
    b = await _branch(db_session)
    ig = await _channel(db_session, b.id, ChannelKind.INSTAGRAM)
    meta = await _channel(db_session, b.id, ChannelKind.META_BUSINESS)
    lead = Lead(branch_id=b.id)
    db_session.add(lead)
    await db_session.flush()
    t_ig = ChannelThread(lead_id=lead.id, channel_id=ig.id, external_thread_id="ig")
    t_meta = ChannelThread(lead_id=lead.id, channel_id=meta.id, external_thread_id="mb")
    db_session.add(t_ig)
    db_session.add(t_meta)
    await db_session.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    for _ in range(3):
        db_session.add(Outbox(branch_id=b.id, thread_id=t_ig.id, text="x", source="agent",
                              status="sent", sent_at=now))
    db_session.add(Outbox(branch_id=b.id, thread_id=t_meta.id, text="x", source="agent",
                          status="sent", sent_at=now))
    await db_session.flush()

    repo = OutboxRepo(db_session, b.id)
    since = now - timedelta(hours=1)
    assert await repo.count_sent_since(since) == 4              # branch-wide
    assert await repo.count_sent_since(since, ig.id) == 3       # only the IG channel
    assert await repo.count_sent_since(since, meta.id) == 1     # only the Meta channel


class _OkChannel:
    kind = ChannelKind.META_BUSINESS

    async def fetch_inbound(self):  # noqa: ANN201
        return []

    async def send_text(self, external_thread_id, text):  # noqa: ANN001, ANN201
        from app.ports.channel import SendResult
        return SendResult(ok=True, external_message_id="x")

    async def session_status(self):  # noqa: ANN201
        return None


async def test_followup_armed_from_the_threads_own_connector_schedule(db_session) -> None:
    """After a bot send, next_followup_at is armed off THIS channel's schedule — a Meta thread
    uses Meta's shorter cadence, an Instagram thread its own, on the same branch."""
    from app.modules.conversation.outbox import OutboxSender
    b = await _branch(db_session)
    ig = await _channel(db_session, b.id, ChannelKind.INSTAGRAM)
    meta = await _channel(db_session, b.id, ChannelKind.META_BUSINESS)
    # branch default enables follow-ups; each connector overrides the schedule (first step differs)
    db_session.add(AppSetting(branch_id=b.id, channel_id=None,
                              key="followup_enabled", value="true"))
    db_session.add(AppSetting(branch_id=b.id, channel_id=ig.id,
                              key="followup_schedule_h", value="6,24,72"))
    db_session.add(AppSetting(branch_id=b.id, channel_id=meta.id,
                              key="followup_schedule_h", value="2,6"))
    lead = Lead(branch_id=b.id)
    db_session.add(lead)
    await db_session.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    armed: dict[str, float] = {}
    for name, ch in (("ig", ig), ("meta", meta)):
        thread = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id=name)
        db_session.add(thread)
        await db_session.flush()
        db_session.add(Outbox(branch_id=b.id, thread_id=thread.id, text="hi", source="agent",
                              status="pending", scheduled_at=now - timedelta(seconds=1)))
        await db_session.flush()
        invalidate(b.id)
        await OutboxSender(db_session, b.id, _OkChannel()).send_next(thread.id)
        await db_session.refresh(thread)
        armed[name] = (thread.next_followup_at - now).total_seconds() / 3600

    assert round(armed["ig"]) == 6     # Instagram's first step
    assert round(armed["meta"]) == 2   # Meta's shorter first step

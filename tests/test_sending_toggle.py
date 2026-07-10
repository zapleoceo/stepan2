"""sending_enabled must be a hard stop on send_outbox, independent of the bot on/off
toggle (agent_enabled) — the lever for 'account got soft-blocked, keep capturing
incoming and queueing replies, but don't touch the channel until I say so'."""
from __future__ import annotations

from sqlalchemy import select

from app.adapters.db.models import Branch, Outbox
from app.modules.settings.service import BranchSettings
from app.worker import main as worker_main
from app.worker import wiring

_SENDING_OFF = BranchSettings(
    agent_enabled=True, hourly_cap=99, daily_cap=99, quiet_start=0, quiet_end=0,
    reply_delay_min_s=0, reply_delay_max_s=0, tz_offset_h=7, tg_group_id="",
    followup_enabled=True, followup_schedule_h=[4, 24, 72],
    tech_search_enabled=False, tech_usecase_enabled=True, daily_budget_usd=0.0,
    crm_enabled=False, crm_webhook_url="", meta_pixel_id="", meta_capi_token="",
    sending_enabled=False,
)


async def test_send_outbox_skips_branch_when_sending_disabled(db_session, monkeypatch) -> None:
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()
    db_session.add(Outbox(branch_id=b.id, thread_id=1, text="hi", source="agent"))
    await db_session.flush()

    async def _fake_active_branches(_session):
        return [b]

    async def _fake_get_settings(_session, _branch_id):
        return _SENDING_OFF

    touched = {"channels": False, "threads": False}

    async def _fake_active_channels(_session, _branch_id):
        touched["channels"] = True
        return []

    async def _fake_threads_with_pending(_session, _branch_id):
        touched["threads"] = True
        return [1]

    async def _platform_on(_session):
        return True

    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_on)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)
    monkeypatch.setattr(worker_main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(wiring, "active_channels", _fake_active_channels)
    monkeypatch.setattr(wiring, "threads_with_pending_outbox", _fake_threads_with_pending)

    attempted = await worker_main.send_outbox({})
    assert attempted == 0
    assert touched == {"channels": False, "threads": False}  # branch skipped entirely

    row = (await db_session.execute(
        select(Outbox).where(Outbox.branch_id == b.id)
    )).scalar_one()
    assert row.status == "pending"  # queue untouched — still there for when sending resumes


async def test_send_outbox_halts_when_platform_kill_switch_off(db_session, monkeypatch) -> None:
    """The emergency platform switch must stop the REAL IG writes, not just generation —
    with it OFF, send_outbox drains nothing (and doesn't even enumerate branches)."""
    reached = {"branches": False}

    async def _platform_off(_session):
        return False

    async def _fake_active_branches(_session):
        reached["branches"] = True
        return []

    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_off)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)

    attempted = await worker_main.send_outbox({})
    assert attempted == 0
    assert reached["branches"] is False  # short-circuited before touching any branch

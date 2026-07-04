"""Quiet hours must throttle proactive follow-ups only, never a reply to an inbound
message — a lead who writes at 3am still gets answered; only the bot-initiated nudge
waits for daytime. Real incident: reply_pending used to skip a whole branch during its
quiet window, leaving real leads unanswered for hours after they wrote in."""
from __future__ import annotations

from app.adapters.db.models import Branch
from app.modules.settings.service import BranchSettings
from app.worker import main as worker_main
from app.worker import wiring

_ALWAYS_QUIET = BranchSettings(
    agent_enabled=True, hourly_cap=99, daily_cap=99, quiet_start=0, quiet_end=24,
    reply_delay_min_s=0, reply_delay_max_s=0, tz_offset_h=7, tg_group_id="",
    followup_enabled=True, followup_schedule_h=[4, 24, 72], knowledge_backend="direct",
    tech_search_enabled=False, tech_usecase_enabled=True, daily_budget_usd=0.0,
    crm_enabled=False, crm_webhook_url="", meta_pixel_id="", meta_capi_token="",
)


async def test_reply_pending_ignores_quiet_hours(db_session, monkeypatch) -> None:
    assert _ALWAYS_QUIET.is_quiet_hour() is True  # sanity: the fixture IS quiet right now

    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()

    called: list[int] = []

    async def _fake_platform_on(_session) -> bool:
        return True

    async def _fake_active_branches(_session):
        return [b]

    async def _fake_get_settings(_session, _branch_id):
        return _ALWAYS_QUIET

    async def _fake_threads_awaiting_reply(_session, branch_id):
        called.append(branch_id)
        return []  # stop here — proves reply_pending reached past the quiet-hour gate

    monkeypatch.setattr(worker_main, "_platform_agent_on", _fake_platform_on)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)
    monkeypatch.setattr(worker_main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(wiring, "threads_awaiting_reply", _fake_threads_awaiting_reply)

    await worker_main.reply_pending({})
    assert called == [b.id]  # reached threads_awaiting_reply despite is_quiet_hour()=True


async def test_schedule_followups_still_respects_quiet_hours(db_session, monkeypatch) -> None:
    """The proactive nudge path is the one quiet hours are FOR — it must still skip."""
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()

    ran: list[int] = []

    async def _fake_platform_on(_session) -> bool:
        return True

    async def _fake_active_branches(_session):
        return [b]

    async def _fake_get_settings(_session, _branch_id):
        return _ALWAYS_QUIET

    class _BoomFollowupService:
        def __init__(self, *_a, **_kw) -> None:
            ran.append(1)

        async def run(self) -> int:
            raise AssertionError("FollowupService.run must not be called during quiet hours")

    monkeypatch.setattr(worker_main, "_platform_agent_on", _fake_platform_on)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)
    monkeypatch.setattr(worker_main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(worker_main, "FollowupService", _BoomFollowupService)

    sent = await worker_main.schedule_followups({})
    assert sent == 0
    assert ran == []  # FollowupService never even constructed

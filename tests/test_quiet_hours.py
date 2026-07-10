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
    followup_enabled=True, followup_schedule_h=[4, 24, 72],
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


async def test_schedule_followups_queues_during_quiet_hours(db_session, monkeypatch) -> None:
    """Queueing (generation) is NOT held by quiet hours — only the SEND is (see
    OutboxSender.send_next). A nudge queued at 23:50 must be sitting ready to go out the
    instant quiet hours lift, not lose the whole cron cycle waiting to even be generated."""
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()

    constructed: list[int] = []
    queued_calls: list[tuple[int, str | None, int]] = []

    async def _fake_platform_on(_session) -> bool:
        return True

    async def _fake_active_branches(_session):
        return [b]

    async def _fake_get_settings(_session, _branch_id):
        return _ALWAYS_QUIET

    async def _fake_effective_kb_branch(_session, branch_id):
        return branch_id

    class _FakeFollowupService:
        def __init__(self, *_a, **_kw) -> None:
            constructed.append(1)

        async def due_threads(self, _now):
            return [(101, "course-a", 0), (102, None, 1), (103, "course-b", 0)]

        async def queue_one(self, thread_id, product_slug, sent_so_far):
            queued_calls.append((thread_id, product_slug, sent_so_far))
            return True

    monkeypatch.setattr(worker_main, "_platform_agent_on", _fake_platform_on)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)
    monkeypatch.setattr(worker_main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(worker_main, "effective_kb_branch", _fake_effective_kb_branch)
    monkeypatch.setattr(worker_main, "FollowupService", _FakeFollowupService)

    queued = await worker_main.schedule_followups({})
    assert queued == 3
    # one FollowupService per branch-bookkeeping call PLUS one per queued thread (each
    # thread now opens its own transaction/service instance — see _queue_one_followup)
    assert len(constructed) == 1 + 3
    assert queued_calls == [(101, "course-a", 0), (102, None, 1), (103, "course-b", 0)]


async def test_one_thread_failing_does_not_discard_others_this_cycle(
    db_session, monkeypatch,
) -> None:
    """Real incident (2026-07-07): the whole branch's due-thread loop used to share ONE
    open transaction, so a job-timeout (or any later thread raising) rolled back every
    follow-up already generated earlier in the same cycle — a broker call could log
    ok=True and still never reach the outbox. Each thread must now commit independently:
    an earlier thread's success must survive a later thread's failure."""
    b = Branch(name="Q", lang="id")
    db_session.add(b)
    await db_session.flush()

    session_opens = 0

    async def _fake_platform_on(_session) -> bool:
        return True

    async def _fake_active_branches(_session):
        return [b]

    async def _fake_get_settings(_session, _branch_id):
        return _ALWAYS_QUIET

    async def _fake_effective_kb_branch(_session, branch_id):
        return branch_id

    class _FakeFollowupService:
        def __init__(self, *_a, **_kw) -> None:
            pass

        async def due_threads(self, _now):
            return [(1, None, 0), (2, None, 0), (3, None, 0)]

        async def queue_one(self, thread_id, _product_slug, _sent_so_far):
            if thread_id == 2:
                raise RuntimeError("simulated broker/db failure on thread 2")
            return True

    class _CountingScope:
        """Each `async with session_scope()` opened is a separate transaction — the
        fix under test. Counting opens proves thread 1/3 each got their own, independent
        of thread 2 blowing up."""
        async def __aenter__(self):
            nonlocal session_opens
            session_opens += 1
            return db_session

        async def __aexit__(self, *_exc) -> bool:
            return False  # never swallow — same contract as the real session_scope

    monkeypatch.setattr(worker_main, "_platform_agent_on", _fake_platform_on)
    monkeypatch.setattr(wiring, "active_branches", _fake_active_branches)
    monkeypatch.setattr(worker_main, "get_settings", _fake_get_settings)
    monkeypatch.setattr(worker_main, "effective_kb_branch", _fake_effective_kb_branch)
    monkeypatch.setattr(worker_main, "FollowupService", _FakeFollowupService)
    monkeypatch.setattr(worker_main, "session_scope", lambda: _CountingScope())

    queued = await worker_main.schedule_followups({})
    assert queued == 2  # threads 1 and 3 succeeded despite thread 2 raising
    # 1 open for platform-flag+branch-listing + 1 for branch bookkeeping + 1 per thread (3)
    # — thread 2's failure didn't prevent thread 3's own independent transaction from
    # opening and succeeding
    assert session_opens == 1 + 1 + 3


async def test_process_deletions_gated_by_platform_kill_switch(monkeypatch) -> None:
    """An unsend is a real outbound IG write — the platform kill-switch must stop it:
    process_deletions returns 0 and never enumerates branches when the switch is off."""
    enumerated: list[int] = []

    async def _platform_off(_session) -> bool:
        return False

    async def _branches_spy(_session):
        enumerated.append(1)
        return []

    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_off)
    monkeypatch.setattr(wiring, "active_branches", _branches_spy)
    assert await worker_main.process_deletions({}) == 0
    assert enumerated == []  # short-circuited before touching any branch


async def test_refresh_and_backfill_gated_by_platform_kill_switch(monkeypatch) -> None:
    """Both IG-private-API maintenance crons must also stop when the platform switch is off."""
    seen: list[str] = []

    async def _platform_off(_session) -> bool:
        return False

    async def _branches_spy(_session):
        seen.append("branches")
        return []

    monkeypatch.setattr(worker_main, "_platform_agent_on", _platform_off)
    monkeypatch.setattr(wiring, "active_branches", _branches_spy)
    assert await worker_main.refresh_profiles({}) == 0
    assert await worker_main.backfill_media({}) == 0
    assert seen == []  # neither enumerated branches

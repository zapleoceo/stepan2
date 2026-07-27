"""Dormant reactivation harvest respects the opt-in flag, cooldown window, gap and cap."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.adapters.db.models import Branch, Channel, ChannelThread, Lead, StageEvent
from app.domain.enums import ChannelKind, Stage
from app.modules.conversation.reactivation import (
    MAX_DORMANT_DAYS,
    REACTIVATION_CAP,
    ReactivationService,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _setup(s):
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="ig", account_id="ig",
                 is_active=True)
    s.add(ch)
    await s.flush()
    return b.id, ch.id


async def _dormant_lead(s, bid, chid, *, days_ago: float, reacts=0, last_react_days=None):
    lead = Lead(branch_id=bid, stage=Stage.DORMANT, agent_enabled=False)
    s.add(lead)
    await s.flush()
    now = _now()
    t = ChannelThread(lead_id=lead.id, channel_id=chid, external_thread_id=f"t{lead.id}",
                      last_in_at=now - timedelta(days=days_ago),
                      last_out_at=now - timedelta(days=days_ago + 0.1))
    s.add(t)
    for _ in range(reacts):
        when = now - timedelta(days=last_react_days if last_react_days is not None else 30)
        s.add(StageEvent(branch_id=bid, lead_id=lead.id, thread_id=None,
                         from_stage="dormant", to_stage="nurturing", actor="system",
                         reason="reactivation", created_at=when))
    await s.flush()
    return t.id, lead.id


def _svc(s, bid, *, enabled=True):
    settings = SimpleNamespace(agent_enabled=True, reactivation_enabled=enabled)
    return ReactivationService(s, bid, llm=None, knowledge=None, settings=settings)


async def test_disabled_returns_nothing(db_session) -> None:
    bid, chid = await _setup(db_session)
    await _dormant_lead(db_session, bid, chid, days_ago=5)
    assert await _svc(db_session, bid, enabled=False).due(_now()) == []


async def test_cooldown_window(db_session) -> None:
    bid, chid = await _setup(db_session)
    good, _ = await _dormant_lead(db_session, bid, chid, days_ago=5)      # in [3,21] → due
    await _dormant_lead(db_session, bid, chid, days_ago=1)                # too soon → skip
    await _dormant_lead(db_session, bid, chid, days_ago=MAX_DORMANT_DAYS + 5)  # too old → skip
    due = await _svc(db_session, bid).due(_now())
    assert [t for t, _s, _l in due] == [good], due


async def test_gap_and_cap(db_session) -> None:
    bid, chid = await _setup(db_session)
    fresh, _ = await _dormant_lead(db_session, bid, chid, days_ago=6)
    # already reactivated 3 days ago (< 14-day gap) → skip
    await _dormant_lead(db_session, bid, chid, days_ago=6, reacts=1, last_react_days=3)
    # already reactivated CAP times (long ago) → skip
    await _dormant_lead(db_session, bid, chid, days_ago=6, reacts=REACTIVATION_CAP,
                        last_react_days=40)
    due = await _svc(db_session, bid).due(_now())
    assert [t for t, _s, _l in due] == [fresh], due


# ── the touch must know which ad the lead came from ───────────────────────────

async def test_entry_block_names_the_product_the_lead_came_for(db_session) -> None:
    """Reactivation shipped for weeks without passing an entry block, and the reply and
    follow-up paths both pass one. Without it the ad's prefill is the only thing in the prompt
    that looks like a stated interest — and four of the six touches sent on 26-27.07 quoted it
    back: "Kakak tanya soal jadwal, durasi, sama biaya kursus" is Meta's button text, put in
    the mouth of someone who never typed it."""
    from app.adapters.db.models import Product

    bid, _chid = await _setup(db_session)
    db_session.add(Product(branch_id=bid, slug="vibe_coding", title="Vibe Coding",
                           content="x", is_active=True))
    await db_session.flush()

    block = await _svc(db_session, bid)._entry_block("vibe_coding")
    assert block is not None
    assert "Vibe Coding" in block
    assert "WHAT THEY CAME FOR" in block


async def test_no_product_means_no_entry_block(db_session) -> None:
    """A lead with no mapped product gets nothing rather than an empty claim about what they
    wanted — inventing an interest is worse than admitting there is none on file."""
    bid, _chid = await _setup(db_session)
    assert await _svc(db_session, bid)._entry_block(None) is None
    assert await _svc(db_session, bid)._entry_block("no_such_course") is None


def test_the_framing_bans_the_two_wasted_shapes() -> None:
    """'Remind me who you are' and a bare 'still interested?' are the two ways this touch is
    spent for nothing — both were live on 27.07."""
    from app.modules.conversation.reactivation import _REACTIVATION_FRAMING

    assert "masih tertarik" in _REACTIVATION_FRAMING
    assert "remind you who they are" in _REACTIVATION_FRAMING


# ── the queue drains best-first, not newest-first ─────────────────────────────

async def _lead_with(s, bid, chid, *, days_ago: float, phone=None, dossier=None):
    lead = Lead(branch_id=bid, stage=Stage.DORMANT, agent_enabled=False,
                phone_e164=phone, dossier=dossier)
    s.add(lead)
    await s.flush()
    now = _now()
    t = ChannelThread(lead_id=lead.id, channel_id=chid, external_thread_id=f"t{lead.id}",
                      last_in_at=now - timedelta(days=days_ago),
                      last_out_at=now - timedelta(days=days_ago + 0.1))
    s.add(t)
    await s.flush()
    return t.id


async def test_a_lead_who_named_a_goal_outranks_a_newer_silent_one(db_session) -> None:
    """The backlog is ~2700 threads draining slowly, and 2747 of 4052 leads never wrote two
    sentences of their own. Ordering by recency alone parked the people who described a real
    project — an app for migrant workers, a donor database — behind hundreds of one-word ad
    taps that merely happened later."""
    bid, chid = await _setup(db_session)
    silent_new = await _lead_with(db_session, bid, chid, days_ago=4)
    with_goal_old = await _lead_with(
        db_session, bid, chid, days_ago=40,
        dossier='{"job_to_be_done": "bikin aplikasi buat toko", "pains": []}')
    due = await _svc(db_session, bid).due(_now())
    order = [tid for tid, _slug, _lid in due]
    assert order.index(with_goal_old) < order.index(silent_new)


async def test_a_phone_on_file_outranks_a_goal(db_session) -> None:
    """A number means a human can act on the answer, which is worth more than a good opening."""
    bid, chid = await _setup(db_session)
    goal_only = await _lead_with(db_session, bid, chid, days_ago=4,
                                 dossier='{"job_to_be_done": "belajar coding"}')
    with_phone = await _lead_with(db_session, bid, chid, days_ago=40, phone="+628123456789")
    order = [tid for tid, _s, _l in await _svc(db_session, bid).due(_now())]
    assert order.index(with_phone) < order.index(goal_only)


async def test_an_empty_goal_string_does_not_count(db_session) -> None:
    """Discovery writes job_to_be_done="" when it learned nothing — that must not score."""
    bid, chid = await _setup(db_session)
    empty = await _lead_with(db_session, bid, chid, days_ago=4,
                             dossier='{"job_to_be_done": "", "pains": []}')
    real = await _lead_with(db_session, bid, chid, days_ago=40,
                            dossier='{"job_to_be_done": "bikin dashboard", "pains": []}')
    order = [tid for tid, _s, _l in await _svc(db_session, bid).due(_now())]
    assert order.index(real) < order.index(empty)


def test_the_daily_ceiling_is_a_hundred() -> None:
    """34 per pass × 3 passes. The old 40 was set the week of the soft-block and never
    re-derived; the branch sends 334-960 a day and averages about one block signature."""
    from app.modules.conversation.reactivation import BATCH_PER_RUN

    assert BATCH_PER_RUN == 34

"""Parking a lead as dormant, from the one place that now does it.

The move was written out five times across three modules — delivery (hard stop, non-target),
follow-up (a nudge that came back dry) and outbox (undeliverable, schedule exhausted). Every
copy had to remember the same four things, and they did. The risk was the sixth copy, and the
first line someone forgot: a lead marked dormant with the bot still on keeps answering
someone the funnel has recorded as given up on.
"""
from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from app.adapters.db.models import Branch, Lead, StageEvent
from app.domain.enums import Stage
from app.modules.conversation.dormancy import park_dormant


async def _lead(s, stage: Stage = Stage.QUALIFYING) -> tuple[int, Lead]:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    s.add(b)
    await s.flush()
    lead = Lead(branch_id=b.id, stage=stage, agent_enabled=True)
    s.add(lead)
    await s.flush()
    return b.id, lead


async def test_parking_journals_the_move_and_silences_the_bot(db_session) -> None:  # noqa: ANN001
    bid, lead = await _lead(db_session)

    assert await park_dormant(db_session, bid, lead, 1, actor="system", reason="exhausted")
    await db_session.flush()

    assert lead.stage is Stage.DORMANT
    assert lead.agent_enabled is False  # the flag and the funnel must never disagree
    event = (await db_session.exec(select(StageEvent))).first()
    assert event.from_stage == str(Stage.QUALIFYING)  # where it came FROM, for the timeline
    assert event.to_stage == str(Stage.DORMANT)
    assert event.reason == "exhausted"


async def test_parking_an_already_dormant_lead_does_nothing(db_session) -> None:  # noqa: ANN001
    """Returns False so a caller can skip the logging and side effects that go with a real
    transition — and so the timeline is not littered with repeats of the same move."""
    bid, lead = await _lead(db_session, Stage.DORMANT)

    assert not await park_dormant(db_session, bid, lead, 1, actor="system", reason="again")
    assert (await db_session.exec(select(StageEvent))).all() == []


async def test_a_missing_lead_is_not_an_error(db_session) -> None:  # noqa: ANN001
    """Every call site loads the lead by id first; a thread whose lead has gone must not take
    the send loop down with it."""
    assert not await park_dormant(db_session, 1, None, 1, actor="system", reason="x")


async def test_a_lead_a_human_took_over_is_left_alone(db_session) -> None:  # noqa: ANN001
    """A delivery hiccup must not yank back a lead a manager owns."""
    for stage in (Stage.MANAGER, Stage.READY, Stage.HANDED_OFF):
        bid, lead = await _lead(db_session, stage)
        assert not await park_dormant(db_session, bid, lead, 1,
                                      actor="system", reason="undeliverable")
        assert lead.stage is stage


async def test_an_explicit_stop_outranks_whose_lead_it_is(db_session) -> None:  # noqa: ANN001
    """"Stop contacting me" is the one reason that parks a hand-off too: continuing to message
    someone who said that is what turns an annoyed lead into a spam report."""
    bid, lead = await _lead(db_session, Stage.MANAGER)

    assert await park_dormant(db_session, bid, lead, 1, actor="bot", reason="hard_stop",
                              respect_human_led=False)
    assert lead.stage is Stage.DORMANT


async def test_the_caller_can_stamp_when_it_happened(db_session) -> None:  # noqa: ANN001
    """The outbox parks a lead as part of processing a row it already has a timestamp for, and
    the journal should say when the send happened, not when the row was swept."""
    bid, lead = await _lead(db_session)
    when = datetime(2026, 7, 28, 9, 30)

    await park_dormant(db_session, bid, lead, 1, actor="system", reason="exhausted",
                       created_at=when)
    await db_session.flush()

    assert (await db_session.exec(select(StageEvent))).first().created_at == when

"""Whether a public reply actually brought the person into DM.

`dm_sent` reads like an outcome and is not one — it records that our public line contained an
invitation, never that anyone accepted. Nothing closed that loop, so the mission could have
been converting nobody for months and looked exactly like one that worked.

On production the first numbers came out 0 of 7 invited against 2 of 9 merely answered.
Sixteen cases prove nothing; the counter existing is what lets the next hundred mean something.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402

from app.adapters.db.models import (  # noqa: E402
    Branch,
    Channel,
    ChannelThread,
    Lead,
    Message,
    PostComment,
)
from app.domain.clock import utc_now  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402
from app.modules.comments.conversion import Conversion, summarise  # noqa: E402

_NOW = utc_now()


async def _branch(session) -> tuple[int, int]:  # noqa: ANN001
    b = Branch(name="Jakarta", lang="id")
    session.add(b)
    await session.flush()
    ch = Channel(branch_id=b.id, kind=ChannelKind.INSTAGRAM, handle="itstep")
    session.add(ch)
    await session.flush()
    return int(b.id), int(ch.id)


async def _comment(session, bid: int, cid: int, *, status: str,  # noqa: ANN001, PLR0913
                   username: str, pk: str | None, handled_ago_days: int = 1) -> None:
    session.add(PostComment(
        branch_id=bid, channel_id=cid, external_id=f"c-{username}-{status}",
        media_id="m1", text="berapa harganya?", status=status,
        author_username=username, author_pk=pk,
        occurred_at=_NOW - timedelta(days=handled_ago_days + 1),
        handled_at=_NOW - timedelta(days=handled_ago_days)))
    await session.flush()


async def _wrote_dm(session, bid: int, cid: int, *, username: str | None,  # noqa: ANN001, PLR0913
                    pk: str | None, days_after_reply: int) -> None:
    """A lead who sent us a message some days after the public reply went out."""
    lead = Lead(branch_id=bid, ig_username=username, ig_user_id=pk)
    session.add(lead)
    await session.flush()
    th = ChannelThread(channel_id=cid, lead_id=lead.id, external_thread_id=f"t-{lead.id}")
    session.add(th)
    await session.flush()
    session.add(Message(
        branch_id=bid, thread_id=th.id, channel_id=cid, external_id=f"m-{lead.id}",
        direction="in", sent_by="lead", text="halo kak",
        occurred_at=_NOW - timedelta(days=1) + timedelta(days=days_after_reply)))
    await session.flush()


@pytest.mark.asyncio
async def test_the_summary_line_shows_evidence_not_just_a_percentage() -> None:
    """'0%' and '0 of 7' say different things: the second admits how little is known. The
    first invites a decision the data cannot support."""
    line = summarise({"dm_sent": Conversion(replies=7, arrived=0),
                      "replied": Conversion(replies=9, arrived=2)})
    assert "0 из 7" in line
    assert "2 из 9" in line


def test_a_rate_with_no_replies_is_zero_not_a_crash() -> None:
    """An untouched branch has no comments at all, and a panel must render for it."""
    assert Conversion(replies=0, arrived=0).rate == 0.0


def test_the_rate_is_arrivals_over_replies() -> None:
    assert Conversion(replies=4, arrived=1).rate == 0.25


@pytest.mark.asyncio
async def test_someone_who_wrote_after_our_reply_counts(db_session) -> None:  # noqa: ANN001
    """The whole mechanism: public answer, then a DM from the same person."""
    from app.modules.comments.conversion import conversion_by_status

    bid, cid = await _branch(db_session)
    await _comment(db_session, bid, cid, status="dm_sent", username="andi", pk="777")
    await _wrote_dm(db_session, bid, cid, username="andi", pk="777", days_after_reply=2)

    got = await conversion_by_status(db_session, bid)

    assert got["dm_sent"].replies == 1
    assert got["dm_sent"].arrived == 1


@pytest.mark.asyncio
async def test_a_lead_who_wrote_BEFORE_the_reply_is_not_our_win(db_session) -> None:  # noqa: ANN001
    """Someone already in conversation who happens to comment must not be counted as
    converted — that would inflate the number with people the comment never moved."""
    from app.modules.comments.conversion import conversion_by_status

    bid, cid = await _branch(db_session)
    await _comment(db_session, bid, cid, status="dm_sent", username="budi", pk="888")
    await _wrote_dm(db_session, bid, cid, username="budi", pk="888", days_after_reply=-5)

    got = await conversion_by_status(db_session, bid)

    assert got["dm_sent"].arrived == 0


@pytest.mark.asyncio
async def test_a_dm_months_later_is_not_attributed_to_the_comment(db_session) -> None:  # noqa: ANN001
    """Attribution has to end somewhere. A person who clicks an ad two months on is not
    evidence that the public reply worked."""
    from app.modules.comments.conversion import conversion_by_status

    bid, cid = await _branch(db_session)
    await _comment(db_session, bid, cid, status="dm_sent", username="citra", pk="999")
    await _wrote_dm(db_session, bid, cid, username="citra", pk="999", days_after_reply=40)

    got = await conversion_by_status(db_session, bid)

    assert got["dm_sent"].arrived == 0


@pytest.mark.asyncio
async def test_the_numeric_id_wins_over_a_renamed_handle(db_session) -> None:  # noqa: ANN001
    """Handles change; the numeric id does not. Matching on the handle alone would lose the
    person who renamed between commenting and writing in."""
    from app.modules.comments.conversion import conversion_by_status

    bid, cid = await _branch(db_session)
    await _comment(db_session, bid, cid, status="dm_sent", username="old_name", pk="555")
    await _wrote_dm(db_session, bid, cid, username="new_name", pk="555", days_after_reply=1)

    got = await conversion_by_status(db_session, bid)

    assert got["dm_sent"].arrived == 1


@pytest.mark.asyncio
async def test_both_outcomes_are_counted_so_they_can_be_compared(db_session) -> None:  # noqa: ANN001
    """The comparison is the point. If inviting converts no better than being useful, the
    invitation costs goodwill for nothing — and that is a decision about the text, not the code."""
    from app.modules.comments.conversion import conversion_by_status

    bid, cid = await _branch(db_session)
    await _comment(db_session, bid, cid, status="dm_sent", username="dedi", pk="111")
    await _comment(db_session, bid, cid, status="replied", username="eka", pk="222")
    await _wrote_dm(db_session, bid, cid, username="eka", pk="222", days_after_reply=3)

    got = await conversion_by_status(db_session, bid)

    assert got["dm_sent"].arrived == 0
    assert got["replied"].arrived == 1


@pytest.mark.asyncio
async def test_another_branch_never_leaks_into_the_count(db_session) -> None:  # noqa: ANN001
    """Tenant isolation, asserted rather than assumed — the same thing eight sites got wrong."""
    from app.modules.comments.conversion import conversion_by_status

    bid, cid = await _branch(db_session)
    other_bid, other_cid = await _branch(db_session)
    await _comment(db_session, bid, cid, status="dm_sent", username="fajar", pk="333")
    await _wrote_dm(db_session, other_bid, other_cid, username="fajar", pk="333",
                    days_after_reply=1)

    got = await conversion_by_status(db_session, bid)

    assert got["dm_sent"].arrived == 0


def test_the_panel_line_shows_both_outcomes_side_by_side() -> None:
    """The comparison is the reason this strip exists. Showing only the invited number would
    let 0-of-7 read as 'the mechanism is new' rather than 'plain answers did better'."""
    from app.api._routes_comments import _conversion_line

    html = _conversion_line({"dm_sent": Conversion(7, 0), "replied": Conversion(9, 2)}, "ru")

    assert "с приглашением" in html
    assert "просто ответ" in html
    assert ">0</b> из 7" in html
    assert ">2</b> из 9" in html


def test_the_panel_line_is_absent_when_nothing_has_been_answered() -> None:
    """A branch that has never replied to a comment gets no strip at all — an empty metric
    reads as a broken one."""
    from app.api._routes_comments import _conversion_line

    assert _conversion_line({}, "ru") == ""
    assert _conversion_line({"dm_sent": Conversion(0, 0)}, "ru") == ""

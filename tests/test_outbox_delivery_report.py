"""Two-phase delivery: a transport that only QUEUES must not be recorded as having sent.

Every connector we had answered for delivery — instagrapi and the Graph API both return after
the message exists in the conversation. The CRM's sender is the first that does not: its
conversation/send queues and returns immediately, and the outcome arrives later as status 1 or
2 (Victor, 2026-08-05). Writing "sent" there would claim a delivery nobody confirmed, and the
hand-off owed on a failure would never fire because nothing would look again.
"""
from __future__ import annotations

import os
from datetime import timedelta

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.adapters.db.models import Branch, Outbox  # noqa: E402
from app.domain.clock import utc_now  # noqa: E402
from app.modules.conversation import delivery_report  # noqa: E402
from app.modules.conversation.repository import OutboxRepo  # noqa: E402


async def _branch(db_session) -> int:  # noqa: ANN001
    b = Branch(name="T", lang="id")
    db_session.add(b)
    await db_session.flush()
    return b.id


async def _queued(db_session, branch_id: int, ref: str, **kw) -> Outbox:  # noqa: ANN001
    row = Outbox(branch_id=branch_id, thread_id=1, text="halo", status="queued",
                 external_ref=ref, sent_at=kw.pop("sent_at", utc_now()), **kw)
    db_session.add(row)
    await db_session.flush()
    return row


async def test_a_success_report_resolves_the_row_to_sent(db_session) -> None:
    b = await _branch(db_session)
    row = await _queued(db_session, b, "wamid.A")

    got = await delivery_report.resolve(db_session, b, "wamid.A", delivered=True)

    assert got is not None
    assert row.status == "sent"
    assert row.error is None


async def test_a_failure_report_resolves_to_failed_and_keeps_the_reason(db_session) -> None:
    b = await _branch(db_session)
    row = await _queued(db_session, b, "wamid.B")

    await delivery_report.resolve(db_session, b, "wamid.B",
                                  delivered=False, error="provider rejected")

    assert row.status == "failed"
    assert row.error == "provider rejected"


async def test_a_repeated_report_changes_nothing(db_session) -> None:
    """Their side retries, and a poll can race a callback. A second report must not move a
    resolved row — least of all drag a `sent` one back to `failed`."""
    b = await _branch(db_session)
    row = await _queued(db_session, b, "wamid.C")
    await delivery_report.resolve(db_session, b, "wamid.C", delivered=True)

    again = await delivery_report.resolve(db_session, b, "wamid.C",
                                          delivered=False, error="late failure")

    assert again is None
    assert row.status == "sent"


async def test_a_report_cannot_reach_another_branch_row(db_session) -> None:
    """The reference comes from another company's system. Matching on it alone would let one
    tenant's delivery report resolve another tenant's message."""
    mine = await _branch(db_session)
    theirs = await _branch(db_session)
    row = await _queued(db_session, theirs, "wamid.SHARED")

    got = await delivery_report.resolve(db_session, mine, "wamid.SHARED", delivered=True)

    assert got is None
    assert row.status == "queued"


async def test_a_failed_delivery_keeps_its_handover_timestamp(db_session) -> None:
    """sent_at records when we handed the message over, which is true regardless of the
    outcome and is what the anti-ban cap counted. Blanking it would hand the branch back
    budget for a message the platform already saw."""
    b = await _branch(db_session)
    at = utc_now() - timedelta(minutes=5)
    row = await _queued(db_session, b, "wamid.D", sent_at=at)

    await delivery_report.resolve(db_session, b, "wamid.D", delivered=False)

    assert row.sent_at == at


async def test_queued_lines_count_against_the_anti_ban_cap(db_session) -> None:
    """The cap exists so an account does not LOOK like a bot, and the platform has already
    seen a queued message. Counting only `sent` would let an async connector blow straight
    through the cap while every row said the budget was untouched."""
    b = await _branch(db_session)
    await _queued(db_session, b, "wamid.E")

    n = await OutboxRepo(db_session, b).count_sent_since(utc_now() - timedelta(hours=1))

    assert n == 1


async def test_stale_queued_rows_are_surfaced_not_guessed(db_session) -> None:
    """"No report" is not "not delivered". Guess sent and an unanswered lead looks answered;
    guess failed and a hand-off fires for a message the customer already read."""
    b = await _branch(db_session)
    old = await _queued(db_session, b, "wamid.OLD", sent_at=utc_now() - timedelta(hours=2))
    await _queued(db_session, b, "wamid.NEW")

    stale = await delivery_report.stale_queued(db_session, b, older_than_min=60)

    assert [r.external_ref for r in stale] == ["wamid.OLD"]
    assert old.status == "queued", "surfacing must not resolve anything"

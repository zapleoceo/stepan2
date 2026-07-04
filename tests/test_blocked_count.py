"""fetch_blocked_count: blocked leads counted per-branch — is_blocked is a lead flag, not a
funnel stage, so without this count (and the funnel's clickable chip) they were unfindable."""
from __future__ import annotations

from app.adapters.db.models import Branch, Lead
from app.api._query import fetch_blocked_count


async def test_fetch_blocked_count_scopes_by_branch(db_session) -> None:
    b1 = Branch(name="B1", lang="id")
    b2 = Branch(name="B2", lang="id")
    db_session.add_all([b1, b2])
    await db_session.flush()
    db_session.add_all([
        Lead(branch_id=b1.id, is_blocked=True),
        Lead(branch_id=b1.id, is_blocked=True),
        Lead(branch_id=b1.id, is_blocked=False),
        Lead(branch_id=b2.id, is_blocked=True),
    ])
    await db_session.flush()

    assert await fetch_blocked_count(db_session, [b1.id]) == 2
    assert await fetch_blocked_count(db_session, [b2.id]) == 1
    assert await fetch_blocked_count(db_session, None) == 3  # unscoped = all branches

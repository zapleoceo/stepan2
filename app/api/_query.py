"""Shared SQL query helpers for UI route handlers."""
from __future__ import annotations

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession


def _branch_where(
    branch_ids: list[int] | None,
    col: str = "branch_id",
) -> tuple[str, dict]:
    """Return (where_clause, params) for branch-scoped SELECT queries."""
    if branch_ids:
        return f"WHERE {col} = ANY(:bids)", {"bids": branch_ids}
    return "", {}


async def fetch_messages(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                "SELECT id, direction, sent_by, text, occurred_at, llm_info FROM message"
                " WHERE thread_id = :tid ORDER BY occurred_at, id"
            ),
            {"tid": thread_id},
        )
    ).all()


async def fetch_pending(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                "SELECT id, text, scheduled_at FROM outbox"
                " WHERE thread_id = :tid AND status = 'pending' ORDER BY id"
            ),
            {"tid": thread_id},
        )
    ).all()


async def fetch_coach_data(session: AsyncSession, branch_id: int) -> tuple[list, list]:
    """Fetch coaching edits (ASC) and active notes for a branch."""
    edits = (
        await session.execute(
            text(
                "SELECT id, request, status, slug, old_text, new_text, summary, created_at"
                " FROM coaching_edit WHERE branch_id = :bid ORDER BY id ASC LIMIT 60"
            ),
            {"bid": branch_id},
        )
    ).all()
    notes = (
        await session.execute(
            text(
                "SELECT id, text FROM coaching_note"
                " WHERE branch_id = :bid AND active = true ORDER BY id"
            ),
            {"bid": branch_id},
        )
    ).all()
    return list(edits), list(notes)

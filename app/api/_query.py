"""Shared SQL query helpers for UI route handlers."""
from __future__ import annotations

from sqlalchemy import case, func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Lead

_PIPELINE_STAGES = ("nurturing", "qualifying", "presenting", "objection")
_WON_STAGES = ("ready", "handed_off")


async def fetch_ad_funnel(session: AsyncSession, branch_ids: list[int] | None) -> list:
    """Per-ad funnel: leads from each ad, counted by pipeline / won / dormant.

    ORM (not raw ANY) so it runs on SQLite too. Rows: (ad_id, ad_media_id, total,
    pipeline, won, dormant), busiest ad first."""
    won = func.sum(case((Lead.stage.in_(_WON_STAGES), 1), else_=0))
    q = (
        select(
            ChannelThread.ad_id,
            ChannelThread.ad_media_id,
            func.count().label("total"),
            func.sum(case((Lead.stage.in_(_PIPELINE_STAGES), 1), else_=0)).label("pipeline"),
            won.label("won"),
            func.sum(case((Lead.stage == "dormant", 1), else_=0)).label("dormant"),
        )
        .join(Lead, Lead.id == ChannelThread.lead_id)  # type: ignore[arg-type]
        .where(ChannelThread.ad_id.is_not(None))  # type: ignore[union-attr]
        .group_by(ChannelThread.ad_id, ChannelThread.ad_media_id)
        .order_by(func.count().desc())
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    return list((await session.execute(q)).all())


def _branch_where(
    branch_ids: list[int] | None,
    col: str = "branch_id",
) -> tuple[str, dict]:
    """Return (where_clause, params) for branch-scoped SELECT queries."""
    if branch_ids:
        return f"WHERE {col} = ANY(:bids)", {"bids": branch_ids}
    return "", {}


_MSG_COLS = (
    "m.id, m.direction, m.sent_by, m.text, m.occurred_at, m.llm_info,"
    " m.link_url, m.preview_url,"
    " (SELECT ma.id FROM media_asset ma WHERE ma.message_id = m.id"
    "  AND ma.data IS NOT NULL ORDER BY ma.id LIMIT 1) AS media_id,"
    " (SELECT ma.kind FROM media_asset ma WHERE ma.message_id = m.id"
    "  AND ma.data IS NOT NULL ORDER BY ma.id LIMIT 1) AS media_kind"
)


async def fetch_messages(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS} FROM message m"  # noqa: S608
                " WHERE m.thread_id = :tid ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id},
        )
    ).all()


async def fetch_messages_since(session: AsyncSession, thread_id: int, after_id: int) -> list:
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS} FROM message m"  # noqa: S608
                " WHERE m.thread_id = :tid AND m.id > :after ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id, "after": after_id},
        )
    ).all()


async def fetch_pending(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                "SELECT id, text, scheduled_at FROM outbox"
                " WHERE thread_id = :tid AND status = 'pending' ORDER BY scheduled_at, id"
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

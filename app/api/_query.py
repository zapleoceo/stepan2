"""Shared SQL query helpers for UI route handlers."""
from __future__ import annotations

from sqlalchemy import case, func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, ChannelThread, Lead

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


# A cleared thread hides pre-cutoff messages from the chat window too (not just the LLM
# prompt) — the rows stay in the DB and in IG, they're just filtered from the view. Strict
# `>` (not `>=`) matches MessageRepo.dialog()'s `since` boundary so the window shown to the
# manager and the window sent to the LLM never disagree on a message landing exactly on cutoff.
_NOT_CLEARED = (
    " AND (ct.context_cleared_at IS NULL OR m.occurred_at > ct.context_cleared_at)"
)


async def fetch_messages(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS} FROM message m"  # noqa: S608
                " JOIN channel_thread ct ON ct.id = m.thread_id"
                f" WHERE m.thread_id = :tid{_NOT_CLEARED} ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id},
        )
    ).all()


async def fetch_messages_since(session: AsyncSession, thread_id: int, after_id: int) -> list:
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS} FROM message m"  # noqa: S608
                " JOIN channel_thread ct ON ct.id = m.thread_id"
                f" WHERE m.thread_id = :tid AND m.id > :after{_NOT_CLEARED}"
                " ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id, "after": after_id},
        )
    ).all()


async def fetch_pending(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                "SELECT id, text, scheduled_at, llm_info, tr_text FROM outbox"
                " WHERE thread_id = :tid AND status = 'pending' ORDER BY scheduled_at, id"
            ),
            {"tid": thread_id},
        )
    ).all()


async def fetch_broker_log(
    session: AsyncSession, branch_ids: list[int] | None, page: int, size: int,
) -> tuple[list, int]:
    """One page of broker_log (newest first) + total count, branch-scoped for non-owners.

    ORM select (not raw ANY) so it runs on SQLite too. Rows are BrokerLog instances."""
    from app.adapters.db.models import BrokerLog  # noqa: PLC0415 (avoid import cycle)
    base = select(BrokerLog)
    count_q = select(func.count()).select_from(BrokerLog)
    if branch_ids:
        base = base.where(BrokerLog.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
        count_q = count_q.where(BrokerLog.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    total = (await session.execute(count_q)).scalar() or 0
    rows = (
        await session.execute(
            base.order_by(BrokerLog.id.desc()).limit(size).offset(page * size)  # type: ignore[attr-defined]
        )
    ).scalars().all()
    return list(rows), int(total)


async def fetch_branch_tz(session: AsyncSession, branch_ids: list[int]) -> dict[int, int]:
    """tz_offset_h per branch id — lets a multi-branch view (e.g. the broker log, which
    spans every branch for the owner) render each row in ITS OWN branch-local time."""
    if not branch_ids:
        return {}
    rows = (
        await session.execute(
            select(Branch.id, Branch.tz_offset_h).where(Branch.id.in_(branch_ids))  # type: ignore[attr-defined]
        )
    ).all()
    return {bid: int(offset) for bid, offset in rows}


async def fetch_discovery_metrics(
    session: AsyncSession, branch_ids: list[int] | None,
) -> dict[str, float | int]:
    """Discovery-before-presentation KPIs from stage_event: of leads that reached
    'presenting', how many had a 'qualifying' (discovery) event first, and the average
    number of inbound messages before the first presentation. Portable (SQLite + Postgres)."""
    if branch_ids:
        keys = ",".join(f":b{i}" for i in range(len(branch_ids)))
        branch_and = f" AND se.branch_id IN ({keys})"
        params = {f"b{i}": b for i, b in enumerate(branch_ids)}
    else:
        branch_and, params = "", {}
    # branch_and holds only fixed :bN placeholders; all values are bound params
    sql = (
        "WITH fp AS (SELECT se.lead_id, MIN(se.created_at) AS t FROM stage_event se"  # noqa: S608
        " WHERE se.to_stage=:pres" + branch_and + " GROUP BY se.lead_id),"
        " dl AS ("
        "  SELECT fp.lead_id, count(m.id) AS cnt FROM fp"
        "  JOIN channel_thread ct ON ct.lead_id = fp.lead_id"
        "  JOIN message m ON m.thread_id = ct.id AND m.direction='in' AND m.occurred_at < fp.t"
        "  GROUP BY fp.lead_id)"
        " SELECT (SELECT count(*) FROM fp) AS reached,"
        "  (SELECT count(*) FROM fp WHERE lead_id IN ("
        "     SELECT se.lead_id FROM stage_event se JOIN fp f2 ON f2.lead_id=se.lead_id"
        "     WHERE se.to_stage=:qual AND se.created_at <= f2.t)) AS discovered,"
        "  (SELECT avg(cnt) FROM dl) AS avg_msgs"
    )
    params.update(pres="presenting", qual="qualifying")
    row = (await session.execute(text(sql), params)).first()
    reached = int(row[0] or 0) if row else 0
    discovered = int(row[1] or 0) if row else 0
    avg_msgs = round(float(row[2]), 1) if row and row[2] is not None else 0.0
    pct = round(discovered / reached * 100, 0) if reached else 0.0
    return {"reached": reached, "discovered": discovered, "pct": pct, "avg_msgs": avg_msgs}


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

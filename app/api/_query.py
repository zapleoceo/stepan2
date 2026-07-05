"""Shared SQL query helpers for UI route handlers."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, ChannelThread, Lead, StageEvent

_PIPELINE_STAGES = ("nurturing", "qualifying", "presenting", "objection")
_WON_STAGES = ("ready", "handed_off")

# Ad-funnel count columns → the exact stage set each number counts, so the chat-list
# links behind those numbers match the counts shown (see _ad_funnel_html / threads_partial).
AD_FUNNEL_GROUPS: dict[str, tuple[str, ...]] = {
    "pipeline": _PIPELINE_STAGES,
    "won": _WON_STAGES,
    "dormant": ("dormant",),
}


async def fetch_ad_funnel(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> list:
    """Per-ad funnel: leads from each ad, counted by pipeline / won / dormant.

    ORM (not raw ANY) so it runs on SQLite too. Rows: (ad_id, ad_media_id, total,
    pipeline, won, dormant), busiest ad first. since/until scope by the lead's
    conversation-start date — the same window as the rest of the reports panel, so
    picking a date range affects this table too, not just the KPIs above it."""
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
    if since is not None:
        q = q.where(Lead.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(Lead.created_at < until)  # type: ignore[attr-defined]
    return list((await session.execute(q)).all())


async def fetch_segment_dist(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> list:
    """Leads by segment (lead_type) with a won count each. Rows: (lead_type, total, won),
    biggest first. NULL lead_type (not yet classified) is bucketed as 'unclear'."""
    won = func.sum(case((Lead.stage.in_(_WON_STAGES), 1), else_=0))
    seg = func.coalesce(Lead.lead_type, "unclear")
    q = (
        select(seg.label("seg"), func.count().label("total"), won.label("won"))
        .group_by(seg)
        .order_by(func.count().desc())
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(Lead.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(Lead.created_at < until)  # type: ignore[attr-defined]
    return list((await session.execute(q)).all())


async def fetch_stage_flow(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> list:
    """Stage-transition edges from the audit log (stage_event), for the funnel-flow diagram.
    Rows: (from_stage, to_stage, count), busiest first. Scoped to leads whose conversation
    started in the window (Lead.created_at) so it agrees with the KPIs and segment tree.
    Reconstructs the real path first-message → every transition → exit, back-edges included."""
    q = (
        select(
            StageEvent.from_stage,
            StageEvent.to_stage,
            func.count().label("n"),
        )
        .join(Lead, Lead.id == StageEvent.lead_id)  # type: ignore[arg-type]
        .where(StageEvent.from_stage != StageEvent.to_stage)  # type: ignore[arg-type]
        .group_by(StageEvent.from_stage, StageEvent.to_stage)
        .order_by(func.count().desc())
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(Lead.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(Lead.created_at < until)  # type: ignore[attr-defined]
    return list((await session.execute(q)).all())


_STAGE_COUNTS_Q = (  # noqa: S608 — {where} comes only from _branch_where
    "SELECT l.stage, COUNT(*) FROM lead l {where} GROUP BY l.stage"
)
_HOUR_Q = (  # noqa: S608 — {and_where} is a fixed branch filter, direction is bound
    # Hour bucket shifted to each message's OWN branch-local time (not UTC) — a Jakarta
    # branch's "peak at 14:00" must mean 14:00 local, not 14:00 UTC (21:00 local).
    "SELECT EXTRACT(HOUR FROM m.occurred_at + make_interval(hours => b.tz_offset_h))::int AS h,"
    " COUNT(*)"
    " FROM message m JOIN channel_thread ct ON ct.id = m.thread_id"
    " JOIN lead l ON l.id = ct.lead_id JOIN branch b ON b.id = l.branch_id"
    " WHERE m.direction = :dir {and_where}"
    " GROUP BY h"
)


async def fetch_stage_counts(
    session: AsyncSession, branch_ids: list[int] | None,
) -> dict[str, int]:
    where, params = _branch_where(branch_ids)
    rows = (
        await session.execute(text(_STAGE_COUNTS_Q.format(where=where)), params)
    ).all()
    return {r[0]: int(r[1]) for r in rows}


async def fetch_bot_enabled_count(
    session: AsyncSession, branch_ids: list[int] | None,
) -> int:
    """How many leads still have the bot switched on (agent_enabled) — the sidebar's
    'bot working N chats' headline. Blocked leads don't count as actively worked."""
    q = select(func.count()).where(Lead.agent_enabled.is_(True), Lead.is_blocked.is_(False))  # type: ignore[attr-defined]
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    return int((await session.execute(q)).scalar_one())


async def fetch_blocked_count(
    session: AsyncSession, branch_ids: list[int] | None,
) -> int:
    """How many leads are blocked — is_blocked is a flag, not a funnel stage, so without
    this count (and the funnel's clickable 🚫 chip) a blocked lead is otherwise unfindable."""
    q = select(func.count()).where(Lead.is_blocked.is_(True))  # type: ignore[attr-defined]
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    return int((await session.execute(q)).scalar_one())


async def fetch_report_data(
    session: AsyncSession, branch_ids: list[int] | None,
) -> tuple[dict[str, int], dict[int, int], dict[int, int], list, dict[str, float | int]]:
    """All datasets for the reports panel: stage counts, in/out hour histograms,
    per-ad funnel and discovery KPIs — the single source for both report routes."""
    and_where = "AND l.branch_id = ANY(:bids)" if branch_ids else ""
    params: dict = {"bids": branch_ids} if branch_ids else {}
    stage_counts = await fetch_stage_counts(session, branch_ids)
    hi = (
        await session.execute(
            text(_HOUR_Q.format(and_where=and_where)), {**params, "dir": "in"}
        )
    ).all()
    ho = (
        await session.execute(
            text(_HOUR_Q.format(and_where=and_where)), {**params, "dir": "out"}
        )
    ).all()
    ad_funnel = await fetch_ad_funnel(session, branch_ids)
    discovery = await fetch_discovery_metrics(session, branch_ids)
    hour_in = {int(r[0]): int(r[1]) for r in hi}
    hour_out = {int(r[0]): int(r[1]) for r in ho}
    return stage_counts, hour_in, hour_out, ad_funnel, discovery


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


# Cleared messages stay visible in the window but greyed and OUT of Stepan's context.
# `excluded` = the row sits before the thread's context_cleared_at cutoff (strict `<=`
# mirrors MessageRepo.dialog()'s `since` so the greyed view and the LLM window agree).
_EXCLUDED_COL = (
    ", (ct.context_cleared_at IS NOT NULL AND m.occurred_at <= ct.context_cleared_at)"
    " AS excluded"
)


async def fetch_messages(session: AsyncSession, thread_id: int) -> list:
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS}{_EXCLUDED_COL} FROM message m"  # noqa: S608
                " JOIN channel_thread ct ON ct.id = m.thread_id"
                " WHERE m.thread_id = :tid ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id},
        )
    ).all()


async def fetch_messages_since(session: AsyncSession, thread_id: int, after_id: int) -> list:
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS}{_EXCLUDED_COL} FROM message m"  # noqa: S608
                " JOIN channel_thread ct ON ct.id = m.thread_id"
                " WHERE m.thread_id = :tid AND m.id > :after"
                " ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id, "after": after_id},
        )
    ).all()


_EVENT_UNION = (  # noqa: S608 — no user-controlled values, thread_id is a bound param
    "SELECT id, 'stage' AS src, to_stage AS kind, from_stage AS detail, actor, created_at"
    " FROM stage_event WHERE thread_id = :tid AND id > :after_stage"
    " UNION ALL"
    " SELECT id, 'log' AS src, kind, detail, actor, created_at"
    " FROM thread_log WHERE thread_id = :tid AND id > :after_log"
)


async def fetch_thread_events(
    session: AsyncSession, thread_id: int, after_stage_id: int = 0, after_log_id: int = 0,
) -> list:
    """Stage transitions + thread-log rows (context clear/load, ...) for one thread,
    time-ordered — rendered as system-log lines interleaved with the message bubbles."""
    return (
        await session.execute(
            text(f"SELECT * FROM ({_EVENT_UNION}) e ORDER BY created_at, id"),  # noqa: S608
            {"tid": thread_id, "after_stage": after_stage_id, "after_log": after_log_id},
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
    since: datetime | None = None, until: datetime | None = None,
) -> dict[str, float | int]:
    """Discovery-before-presentation KPIs from stage_event: of leads that reached
    'presenting', how many had a 'qualifying' (discovery) event first, and the average
    number of inbound messages before the first presentation. Portable (SQLite + Postgres).

    since/until scope by the lead's conversation-start date, joined in via `l` — the same
    window as the rest of the reports panel, so a date filter affects this KPI too."""
    conditions, params = [], {}
    if branch_ids:
        keys = ",".join(f":b{i}" for i in range(len(branch_ids)))
        conditions.append(f"se.branch_id IN ({keys})")
        params.update({f"b{i}": b for i, b in enumerate(branch_ids)})
    if since is not None:
        conditions.append("l.created_at >= :since")
        params["since"] = since
    if until is not None:
        conditions.append("l.created_at < :until")
        params["until"] = until
    # extra_and holds only fixed, hardcoded conditions — all values are bound params
    extra_and = "".join(f" AND {c}" for c in conditions)
    sql = (
        "WITH fp AS (SELECT se.lead_id, MIN(se.created_at) AS t FROM stage_event se"  # noqa: S608
        " JOIN lead l ON l.id = se.lead_id"
        " WHERE se.to_stage=:pres" + extra_and + " GROUP BY se.lead_id),"
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

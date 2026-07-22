"""Shared SQL query helpers for UI route handlers."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import case, func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import (
    AdCreativeMap,
    AdInsightDaily,
    Branch,
    ChannelThread,
    Lead,
    StageEvent,
)
from app.config import settings
from app.domain.clock import utc_now

_PIPELINE_STAGES = ("nurturing", "qualifying", "presenting", "objection")
_WON_STAGES = ("ready", "handed_off")

# Inbox "unanswered" split. AWAITING_BASE = lead spoke last, no reply out yet, not blocked,
# on a WORKING connector (Meta Business is excluded until its connector is finished — those
# chats just hang, they don't count). IN_QUEUE = the chats Stepan actively works: bot on AND in
# a funnel stage where Stepan participates (new/nurturing/qualifying/presenting/objection). The
# complement (base AND NOT in-queue) is everything else unanswered — dormant, handed_off/manager
# (a human owns it), ready, or bot off. The two partition AWAITING_BASE, so they sum to total.
AWAITING_BASE = (
    "ct.last_in_at IS NOT NULL"
    " AND (ct.last_out_at IS NULL OR ct.last_out_at < ct.last_in_at)"
    " AND l.is_blocked = false"
    " AND EXISTS (SELECT 1 FROM channel c WHERE c.id = ct.channel_id"
    "             AND c.kind <> 'meta_business')"
)
IN_QUEUE_EXTRA = (
    "l.agent_enabled = true"
    " AND l.stage IN ('new', 'nurturing', 'qualifying', 'presenting', 'objection')"
)


def awaiting_cutoff() -> datetime:
    """The age floor for the reply queue — a thread whose last inbound is older than this the
    worker does NOT auto-reply (awaiting_reply_max_age_days), so it counts as 'won't reply'."""
    return utc_now() - timedelta(days=settings().awaiting_reply_max_age_days)

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


async def fetch_ad_spend(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> dict[str, dict]:
    """Meta spend + conversation depth per ad_id, summed over the window. ad_id → metrics.

    Reads ONLY the local cache (ad_insight_daily) — never Graph, so the reports page stays a
    plain SQL page. Day granularity is what makes an arbitrary date range a SUM here.

    Keyed by ad_id so the caller can join it to our own per-ad funnel through ad_creative_map;
    the two are reported side by side rather than summed, since they count different things
    (Meta counts conversations it attributed, we count leads we actually hold)."""
    q = (
        select(
            AdInsightDaily.ad_id,
            func.sum(AdInsightDaily.spend).label("spend"),
            func.sum(AdInsightDaily.impressions).label("impressions"),
            func.sum(AdInsightDaily.conv_started).label("conv_started"),
            func.sum(AdInsightDaily.conv_depth_3).label("conv_depth_3"),
            func.sum(AdInsightDaily.conv_depth_5).label("conv_depth_5"),
            func.sum(AdInsightDaily.blocks).label("blocks"),
        )
        .group_by(AdInsightDaily.ad_id)
    )
    if branch_ids:
        q = q.where(AdInsightDaily.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(AdInsightDaily.day >= since.date())  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(AdInsightDaily.day < until.date())  # type: ignore[attr-defined]
    return {
        row.ad_id: {
            "spend": float(row.spend or 0), "impressions": int(row.impressions or 0),
            "conv_started": int(row.conv_started or 0),
            "conv_depth_3": int(row.conv_depth_3 or 0),
            "conv_depth_5": int(row.conv_depth_5 or 0),
            "blocks": int(row.blocks or 0),
        }
        for row in (await session.execute(q)).all()
    }


async def fetch_media_to_ad(
    session: AsyncSession, branch_ids: list[int] | None,
) -> dict[str, dict]:
    """ad_media_id → {ad_id, campaign_name, …}. The join key is the MEDIA pk, never the
    ad_id we store from instagrapi — that one lives in a different id space and resolves to
    code 100 against Graph (see app/modules/ads/bridge.py)."""
    q = select(
        AdCreativeMap.media_pk, AdCreativeMap.ad_id, AdCreativeMap.ad_name,
        AdCreativeMap.campaign_name, AdCreativeMap.objective,
    )
    if branch_ids:
        q = q.where(AdCreativeMap.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    return {
        row.media_pk: {
            "ad_id": row.ad_id, "ad_name": row.ad_name,
            "campaign_name": row.campaign_name, "objective": row.objective,
        }
        for row in (await session.execute(q)).all()
    }


async def fetch_segment_dist(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> list:
    """Leads by (audience, intent segment) with a won count each. Rows:
    (audience, lead_type, total, won). NULL audience → 'unknown' (its own block, NOT lumped
    into adults), NULL lead_type → 'unclear'. The renderer orders; here we just aggregate."""
    won = func.sum(case((Lead.stage.in_(_WON_STAGES), 1), else_=0))
    aud = func.coalesce(Lead.audience, "unknown")  # not-yet-classified stays distinct
    seg = func.coalesce(Lead.lead_type, "unclear")
    q = (
        select(aud.label("aud"), seg.label("seg"),
               func.count().label("total"), won.label("won"))
        .group_by(aud, seg)
        .order_by(func.count().desc())
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(Lead.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(Lead.created_at < until)  # type: ignore[attr-defined]
    return list((await session.execute(q)).all())


async def fetch_audience_segment_stage_dist(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> list:
    """Leads by (audience, intent segment, funnel stage) — the mini-funnel boxes shown to the
    RIGHT of each segment node. Rows: (audience, lead_type, stage, count). NULL audience →
    'unknown', NULL lead_type → 'unclear' (matches fetch_segment_dist's bucketing)."""
    aud = func.coalesce(Lead.audience, "unknown")
    seg = func.coalesce(Lead.lead_type, "unclear")
    q = (
        select(aud.label("aud"), seg.label("seg"),
               Lead.stage.label("stage"), func.count().label("cnt"))
        .group_by(aud, seg, Lead.stage)
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
    # count DISTINCT leads per edge, not raw events: a lead logs several transitions and the
    # S1 history migration double-logged some, so raw event counts overstate the flow. Distinct
    # leads make each link's thickness "how many people moved this way", comparable to the tree.
    n = func.count(func.distinct(StageEvent.lead_id))
    q = (
        select(
            StageEvent.from_stage,
            StageEvent.to_stage,
            n.label("n"),
        )
        .join(Lead, Lead.id == StageEvent.lead_id)  # type: ignore[arg-type]
        .where(StageEvent.from_stage != StageEvent.to_stage)  # type: ignore[arg-type]
        .group_by(StageEvent.from_stage, StageEvent.to_stage)
        .order_by(n.desc())
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(Lead.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(Lead.created_at < until)  # type: ignore[attr-defined]
    return list((await session.execute(q)).all())


async def fetch_stage_reach(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> dict[str, int]:
    """Distinct leads that passed through each stage (touched it as `from` or `to`), for the
    flow node headcounts. This is a real per-stage lead count — always ≤ total leads — unlike
    summing the per-edge link counts, which multi-counts a lead that appears on several edges
    (so the 'new' entry bar could otherwise read higher than the whole lead base)."""
    q = (
        select(StageEvent.lead_id, StageEvent.from_stage, StageEvent.to_stage)
        .join(Lead, Lead.id == StageEvent.lead_id)  # type: ignore[arg-type]
        .where(StageEvent.from_stage != StageEvent.to_stage)  # type: ignore[arg-type]
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(Lead.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(Lead.created_at < until)  # type: ignore[attr-defined]
    seen: dict[str, set[int]] = {}
    movers: set[int] = set()
    for lid, frm, to in (await session.execute(q)).all():
        seen.setdefault(frm, set()).add(lid)
        seen.setdefault(to, set()).add(lid)
        movers.add(lid)
    out = {s: len(ids) for s, ids in seen.items()}
    out["*"] = len(movers)  # distinct leads with any transition — for the "no movement" bucket
    return out


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
    " m.link_url, m.preview_url, mm.media_id, mm.media_kind, mm.media_ready,"
    " m.media_pending"
)

# One media_asset row per message, joined once for the whole thread instead of two
# correlated subqueries re-run per message row. A data=NULL stub (bytes not backfilled yet)
# is no longer filtered out — it's surfaced with media_ready=0 so the bubble can render a
# self-refreshing placeholder; a ready asset still wins (CASE orders data-present first),
# then lowest id. ROW_NUMBER()/CASE work on both Postgres and the SQLite used in tests
# (3.25+), so no dialect branching needed.
_MEDIA_JOIN = (
    " LEFT JOIN ("
    " SELECT message_id, id AS media_id, kind AS media_kind,"
    " (data IS NOT NULL) AS media_ready,"
    " ROW_NUMBER() OVER (PARTITION BY message_id"
    "   ORDER BY (CASE WHEN data IS NOT NULL THEN 0 ELSE 1 END), id) AS rn"
    " FROM media_asset"
    " ) mm ON mm.message_id = m.id AND mm.rn = 1"
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
                f"{_MEDIA_JOIN}"
                " WHERE m.thread_id = :tid AND m.revoked_at IS NULL"
                " ORDER BY m.occurred_at, m.id"
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
                f"{_MEDIA_JOIN}"
                " WHERE m.thread_id = :tid AND m.id > :after AND m.revoked_at IS NULL"
                " ORDER BY m.occurred_at, m.id"
            ),
            {"tid": thread_id, "after": after_id},
        )
    ).all()


async def fetch_message(session: AsyncSession, mid: int):
    """One message row shaped exactly like fetch_messages' rows plus the thread context a
    single-bubble re-render needs (thread id, lead_seen_at, branch guard, tz). Backs the
    self-refreshing pending-media bubble — None if the message is gone."""
    return (
        await session.execute(
            text(
                f"SELECT {_MSG_COLS}{_EXCLUDED_COL},"  # noqa: S608
                " ct.id AS thread_id, ct.lead_seen_at, l.branch_id, b.tz_offset_h"
                " FROM message m"
                " JOIN channel_thread ct ON ct.id = m.thread_id"
                " JOIN lead l ON l.id = ct.lead_id"
                " JOIN branch b ON b.id = l.branch_id"
                f"{_MEDIA_JOIN}"
                " WHERE m.id = :mid"
            ),
            {"mid": mid},
        )
    ).first()


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
    # Include 'failed' rows so a send that never reached the lead (e.g. Meta 24h window closed
    # on a manager reply) shows the manager an error bubble instead of the queued line silently
    # vanishing. 'skipped' rows are automated + expected, so they stay hidden.
    return (
        await session.execute(
            text(
                "SELECT id, text, scheduled_at, llm_info, tr_text, status, error FROM outbox"
                " WHERE thread_id = :tid AND status IN ('pending', 'failed')"
                " ORDER BY scheduled_at, id"
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


_LOG_WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1), "4h": timedelta(hours=4), "12h": timedelta(hours=12),
    "24h": timedelta(days=1), "7d": timedelta(days=7),
}
_TURN_GAP = timedelta(seconds=300)  # same-thread calls within this = one turn (UI grouping)
_HIST_BUCKETS = 24


def log_window_keys() -> list[str]:
    return list(_LOG_WINDOWS)


async def fetch_turn_histogram(
    session: AsyncSession, branch_ids: list[int] | None, window_key: str,
) -> tuple[list[float], int, datetime, float]:
    """End-to-end seconds per time bucket over the window, for the log-header histogram.

    Broker calls are grouped into per-thread TURNS (a same-thread gap over _TURN_GAP starts a
    new turn); each turn's wall-clock (last finish − first start) lands in the bucket of its
    start. Returns (bucket_totals_seconds, turn_count, since, bucket_span_seconds)."""
    from app.adapters.db.models import BrokerLog  # noqa: PLC0415 (avoid import cycle)
    span = _LOG_WINDOWS.get(window_key, _LOG_WINDOWS["24h"])
    since = utc_now() - span
    q = select(BrokerLog.thread_id, BrokerLog.created_at, BrokerLog.latency_ms).where(
        BrokerLog.created_at >= since,
        BrokerLog.thread_id.is_not(None),  # type: ignore[union-attr]
    )
    if branch_ids:
        q = q.where(BrokerLog.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    q = q.order_by(BrokerLog.thread_id, BrokerLog.created_at)  # type: ignore[attr-defined]
    rows = (await session.execute(q)).all()

    buckets = [0.0] * _HIST_BUCKETS
    bucket_span = span / _HIST_BUCKETS
    turns = 0
    cur_tid: int | None = None
    t_start: datetime | None = None
    t_end: datetime | None = None
    prev: datetime | None = None

    def flush() -> None:
        nonlocal turns
        if t_start is None or t_end is None:
            return
        idx = min(max(int((t_start - since) / bucket_span), 0), _HIST_BUCKETS - 1)
        buckets[idx] += (t_end - t_start).total_seconds()
        turns += 1

    for tid, created, lat in rows:
        end = created + timedelta(milliseconds=int(lat or 0))
        if tid != cur_tid or (prev is not None and created - prev > _TURN_GAP):
            flush()
            cur_tid, t_start, t_end = tid, created, end
        else:
            t_end = max(t_end, end) if t_end is not None else end
        prev = created
    flush()
    return buckets, turns, since, bucket_span.total_seconds()


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


async def fetch_closed_in_period(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> int:
    """Leads actually CLOSED inside the window — dated by the ready/handed_off transition.

    Every other KPI on the panel is a cohort metric (scoped by lead.created_at), which is the
    right lens for "how did the leads we got this week behave" but answers a different question
    than "how much did we sell this week". Over 3 days that read 2 while 11 leads really closed:
    9 of them had started their conversation before the window, so the cohort filter hid them
    (2026-07-15). Both numbers are true; the panel now shows this one next to the cohort's."""
    q = (
        select(func.count(func.distinct(StageEvent.lead_id)))
        .join(Lead, Lead.id == StageEvent.lead_id)  # type: ignore[arg-type]
        .where(StageEvent.to_stage.in_(("ready", "handed_off")))  # type: ignore[attr-defined]
    )
    if branch_ids:
        q = q.where(Lead.branch_id.in_(branch_ids))  # type: ignore[attr-defined]
    if since is not None:
        q = q.where(StageEvent.created_at >= since)  # type: ignore[attr-defined]
    if until is not None:
        q = q.where(StageEvent.created_at < until)  # type: ignore[attr-defined]
    return int((await session.execute(q)).scalar_one() or 0)


async def fetch_discovery_metrics(
    session: AsyncSession, branch_ids: list[int] | None,
    since: datetime | None = None, until: datetime | None = None,
) -> dict[str, float | int]:
    """Discovery-before-presentation KPIs: of leads that reached 'presenting', how many had a
    real PAIN captured, and the average number of inbound messages before the first
    presentation. Portable (SQLite + Postgres).

    'discovered' counts a non-empty pains list on the lead's needs profile — NOT a pass through
    the 'qualifying' stage. Stage-based counting read 87% while only 65% of those leads had any
    pain at all (3-day audit, 2026-07-15): qualifying is the default stage every lead crosses,
    so the old KPI measured the funnel's plumbing and always looked healthy. The needs JSON is
    matched textually ('"pains": []' = empty) to stay portable across SQLite and Postgres.

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
        "  (SELECT count(*) FROM fp JOIN lead l2 ON l2.id = fp.lead_id"
        "     WHERE l2.needs LIKE '%\"pains\":%' AND l2.needs NOT LIKE '%\"pains\": []%')"
        "   AS discovered,"
        "  (SELECT avg(cnt) FROM dl) AS avg_msgs"
    )
    params.update(pres="presenting")
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

"""ChannelService — branch-scoped channel lifecycle, incl. the deletion cascade.

Deleting a channel must tear down everything hanging off it (threads → their messages,
media, outbox, alerts, stage events) in FK-safe order, then drop only the leads left
with NO thread at all. A phone-merged lead that still has a thread on another channel of
the same branch MUST survive — leads are merged by phone across channels (see
IdentityService), so a lead is not owned by one channel. Every statement is
branch-scoped and parameterized; the whole purge runs inside the caller's transaction,
so a failure rolls the channel back intact.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# Leads in this branch with no remaining thread on ANY channel — the orphan set.
_ORPHAN = (
    "SELECT l.id FROM lead l WHERE l.branch_id = :b"
    " AND NOT EXISTS (SELECT 1 FROM channel_thread ct WHERE ct.lead_id = l.id)"
)

# Every table holding an FK to the row being deleted, keyed by the parent. Nothing here
# cascades at the DB level, so a table missing from a list aborts the whole purge on a
# ForeignKeyViolation — which is exactly how the "can't delete connector" bug kept coming
# back (lead refs when needs-cloud + CRM landed; thread_log and post_comment after that).
# tests/test_channel_purge_covers_fks.py reads these tuples and fails when a new FK appears,
# so the next feature that adds one is caught in CI rather than in the field.
_BY_THREAD = ("outbox", "manager_alert", "stage_event", "thread_log")
_BY_LEAD = ("manager_alert", "stage_event", "crm_lead_state", "lead_need_tag",
            "need_lead_state", "outbound_comment")
# app_setting rows scoped to a channel are per-connector overrides. Production carries no FK
# for that column (the migration added it without one, unlike the model), so they never
# blocked the delete — they just outlived it, and would have attached themselves to whatever
# channel took the id next.
_BY_CHANNEL = ("post_comment", "outbound_comment", "channel_session", "app_setting")


@dataclass(frozen=True)
class PurgeResult:
    threads: int
    messages: int
    leads: int


class ChannelService:
    """Channel lifecycle for one branch — never touches another tenant's rows."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id

    async def purge(self, channel_id: int) -> PurgeResult | None:
        """Delete a channel and all its conversation data; drop newly-orphaned leads.

        Returns counts, or None if the channel is not in this branch (tenant guard)."""
        p = {"c": channel_id, "b": self.branch_id}
        owns = (await self.session.execute(
            text("SELECT 1 FROM channel WHERE id = :c AND branch_id = :b"), p
        )).first()
        if owns is None:
            return None

        n_threads = await self._count("channel_thread WHERE channel_id = :c", p)
        n_msgs = await self._count("message WHERE channel_id = :c", p)

        # 1) conversation data of THIS channel, children before parents
        await self._exec(
            "DELETE FROM media_asset WHERE message_id IN"
            " (SELECT id FROM message WHERE channel_id = :c)", p)
        await self._exec("DELETE FROM message WHERE channel_id = :c", p)
        for tbl in _BY_THREAD:
            await self._exec(
                f"DELETE FROM {tbl} WHERE thread_id IN"  # noqa: S608
                " (SELECT id FROM channel_thread WHERE channel_id = :c)", p)
        await self._exec("DELETE FROM channel_thread WHERE channel_id = :c", p)

        # 2) leads now orphaned — the threads on this channel were their only ones.
        n_leads = await self._count(f"({_ORPHAN}) AS orphan", p)  # noqa: S608
        for tbl in _BY_LEAD:
            await self._exec(f"DELETE FROM {tbl} WHERE lead_id IN ({_ORPHAN})", p)  # noqa: S608
        await self._exec(f"DELETE FROM lead WHERE id IN ({_ORPHAN})", p)  # noqa: S608

        # 3) the channel itself
        for tbl in _BY_CHANNEL:
            await self._exec(f"DELETE FROM {tbl} WHERE channel_id = :c", p)  # noqa: S608
        await self._exec("DELETE FROM channel WHERE id = :c", p)
        await self.session.flush()

        logger.info(
            "purged channel branch=%d channel=%d threads=%d messages=%d orphan_leads=%d",
            self.branch_id, channel_id, n_threads, n_msgs, n_leads)
        return PurgeResult(threads=n_threads, messages=n_msgs, leads=n_leads)

    async def _count(self, from_where: str, params: dict) -> int:
        row = (await self.session.execute(
            text(f"SELECT COUNT(*) FROM {from_where}"), params  # noqa: S608
        )).scalar()
        return int(row or 0)

    async def _exec(self, sql: str, params: dict) -> None:
        await self.session.execute(text(sql), params)

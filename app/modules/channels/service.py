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
        await self._exec(
            "DELETE FROM outbox WHERE thread_id IN"
            " (SELECT id FROM channel_thread WHERE channel_id = :c)", p)
        await self._exec(
            "DELETE FROM manager_alert WHERE thread_id IN"
            " (SELECT id FROM channel_thread WHERE channel_id = :c)", p)
        await self._exec(
            "DELETE FROM stage_event WHERE thread_id IN"
            " (SELECT id FROM channel_thread WHERE channel_id = :c)", p)
        await self._exec("DELETE FROM channel_thread WHERE channel_id = :c", p)

        # 2) leads now orphaned (threads on this channel were their only ones) + their refs.
        # Every table that FK-references lead.id must be cleared BEFORE the lead, or the delete
        # aborts on a ForeignKeyViolation (this used to silently 500 the whole purge once the
        # needs-cloud + CRM features added new lead references — the "can't delete connector"
        # bug). Keep this list in sync with FKs to lead.id (see models.py).
        n_leads = await self._count(f"({_ORPHAN}) AS orphan", p)  # noqa: S608
        for tbl in ("manager_alert", "stage_event", "crm_lead_state",
                    "lead_need_tag", "need_lead_state"):
            await self._exec(f"DELETE FROM {tbl} WHERE lead_id IN ({_ORPHAN})", p)  # noqa: S608
        await self._exec(f"DELETE FROM lead WHERE id IN ({_ORPHAN})", p)  # noqa: S608

        # 3) the channel itself
        await self._exec("DELETE FROM channel_session WHERE channel_id = :c", p)
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

"""CrmPullService — periodic pull of CRM state for active-funnel leads.

Runs on the sync cron: for the stalest bot-worked leads with a phone, refresh the CRM
state and stand any `hold` lead down proactively (before its next scheduled contact).
The pre-send point-check (CrmGate.allow_send) is the freshness backstop; this keeps the
cache warm and catches leads a manager touched between messages. Gated by crm_read_*.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import CrmLeadState, Lead
from app.modules.crm.gate import CrmGate, CrmReaderPort
from app.modules.settings.service import get_settings

logger = logging.getLogger(__name__)

# Stages the bot still actively works — the only leads worth gating against the CRM.
_ACTIVE = ("new", "nurturing", "qualifying", "presenting", "objection")


class CrmPullService:
    def __init__(
        self, session: AsyncSession, branch_id: int, reader: CrmReaderPort
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.gate = CrmGate(session, branch_id, reader)

    async def sync_active(self, limit: int = 50) -> int:
        cfg = await get_settings(self.session, self.branch_id)
        if not cfg.crm_read_enabled or not (cfg.crm_state_url or "").strip():
            return 0
        leads = await self._stale_active(limit)
        held = 0
        for lead in leads:
            try:
                if await self.gate.enforce(lead) == "hold":
                    held += 1
            except Exception:
                logger.exception("crm pull failed branch=%d lead=%d", self.branch_id, lead.id)
        if leads:
            logger.info("crm pull branch=%d: %d checked, %d held",
                        self.branch_id, len(leads), held)
        return held

    async def _stale_active(self, limit: int) -> list[Lead]:
        """Bot-worked leads with a phone, stalest-first (never-checked before re-checked)."""
        q = (
            select(Lead)
            .outerjoin(CrmLeadState, CrmLeadState.lead_id == Lead.id)
            .where(
                Lead.branch_id == self.branch_id,
                Lead.agent_enabled.is_(True),  # type: ignore[union-attr]
                Lead.phone_e164.is_not(None),  # type: ignore[union-attr]
                Lead.stage.in_(_ACTIVE),  # type: ignore[attr-defined]
            )
            .order_by(CrmLeadState.fetched_at.asc().nulls_first())
            .limit(limit)
        )
        return list((await self.session.execute(q)).scalars().all())

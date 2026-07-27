"""CrmPullService — periodic pull of CRM state, for two different populations.

`sync_active` is the original job and it is a GATE: for the stalest bot-worked leads with a
phone, refresh the CRM state and stand any `hold` lead down before its next scheduled contact.
It asks "should the bot keep talking to this person?", so it looks only at leads the bot still
works — active stages, agent_enabled.

`sync_outcomes` is the opposite population and a different question: "did this lead buy?" The
CRM's MCP already answers it — every state carries `deal_won` — but nobody was asking, because
the gate's filters exclude precisely the leads that could have bought. A lead handed to a
manager is in `ready`/`handed_off`/`manager` (not in _ACTIVE) and has agent_enabled=False (the
hand-off mutes the bot). So all 53 leads that reached a human were invisible to the CRM read,
and the one won deal we know about surfaced only because that lead happened to be polled
earlier, while it was still in the funnel.

This one never stands a lead down — the human already owns it. It only refreshes the cache so
`deal_won` is recorded, which is what the Meta Purchase event and any honest revenue report
are built on. Gated by crm_read_*.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import CrmLeadState, Lead
from app.modules.crm.gate import CrmGate, CrmReaderPort
from app.modules.settings.service import get_settings

logger = logging.getLogger(__name__)

# Stages the bot still actively works — the only leads worth gating against the CRM.
_ACTIVE = ("new", "qualifying", "presenting", "objection", "nurturing")
# …and the stages it has left. A lead only reaches these by being handed to a human, which is
# the one route to a sale, so this is where `deal_won` lives.
_EXITED = ("ready", "handed_off", "manager")


class CrmPullService:
    def __init__(
        self, session: AsyncSession, branch_id: int, reader: CrmReaderPort
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.gate = CrmGate(session, branch_id, reader)

    async def sync_active(self, limit: int = 15, time_budget_s: float = 60.0) -> int:
        """Refresh the stalest active leads. Bounded twice: `limit` leads AND a wall-clock
        budget — an MCP state read costs seconds, and this runs inside a cron job with a
        hard timeout; better to cover fewer leads than to blow the job."""
        cfg = await get_settings(self.session, self.branch_id)
        from app.modules.crm.gate import crm_read_url  # noqa: PLC0415
        if not cfg.crm_read_enabled or not crm_read_url(cfg):
            return 0
        leads = await self._stale_active(limit)
        held = 0
        started = time.monotonic()
        for lead in leads:
            if time.monotonic() - started > time_budget_s:
                logger.info("crm pull branch=%d: time budget hit", self.branch_id)
                break
            try:
                if await self.gate.enforce(lead) == "hold":
                    held += 1
            except Exception:
                logger.exception("crm pull failed branch=%d lead=%d", self.branch_id, lead.id)
        if leads:
            logger.info("crm pull branch=%d: %d checked, %d held",
                        self.branch_id, len(leads), held)
        return held

    async def sync_outcomes(self, limit: int = 25, time_budget_s: float = 60.0) -> int:
        """Refresh CRM state for leads that LEFT the funnel to a human. Returns won deals seen.

        Deliberately no stand-down: a manager already owns these, and `hold` is meaningless
        once the bot is out. This exists only so `deal_won` reaches our side — without it the
        question "how many did we sell?" has no answer at all, which is different from zero.

        A bigger limit than the gate's and a slower cadence: outcomes change over days, not
        between messages, so breadth matters more than freshness here."""
        cfg = await get_settings(self.session, self.branch_id)
        from app.modules.crm.gate import crm_read_url  # noqa: PLC0415
        if not cfg.crm_read_enabled or not crm_read_url(cfg):
            return 0
        leads = await self._stale_exited(limit)
        won = 0
        started = time.monotonic()
        for lead in leads:
            if time.monotonic() - started > time_budget_s:
                logger.info("crm outcomes branch=%d: time budget hit", self.branch_id)
                break
            try:
                state = await self.gate.refresh(lead)
            except Exception:
                logger.exception(
                    "crm outcomes failed branch=%d lead=%d", self.branch_id, lead.id)
                continue
            if state is not None and state.deal_won:
                won += 1
                await self._report_purchase(lead, cfg)
        if leads:
            logger.info("crm outcomes branch=%d: %d checked, %d won",
                        self.branch_id, len(leads), won)
        return won

    async def _report_purchase(self, lead: Lead, cfg) -> None:  # noqa: ANN001
        """Tell Meta the deal closed. Idempotent by event_id, so re-polling a won lead every
        day costs nothing — Meta dedups on it, which is why this needs no 'already sent' flag.

        Best-effort in the strictest sense: ad reporting must never be able to break the CRM
        sync, so every failure is swallowed by the adapter and logged there."""
        from app.adapters.meta_capi import MetaCapi, capi_token  # noqa: PLC0415
        token = capi_token(cfg)
        if not cfg.meta_pixel_id or not token:
            return
        await MetaCapi().send_lead(
            cfg.meta_pixel_id, token,
            event_id=f"purchase-{self.branch_id}-{lead.id}",
            phone=lead.phone_e164,
            event_name="Purchase",
        )

    async def _stale_exited(self, limit: int) -> list[Lead]:
        """Leads that reached a human, stalest-first. No agent_enabled filter — the hand-off
        sets it False, and filtering on it is exactly what hid this population."""
        q = (
            select(Lead)
            .outerjoin(CrmLeadState, CrmLeadState.lead_id == Lead.id)
            .where(
                Lead.branch_id == self.branch_id,
                Lead.phone_e164.is_not(None),  # type: ignore[union-attr]
                Lead.stage.in_(_EXITED),  # type: ignore[attr-defined]
            )
            .order_by(CrmLeadState.fetched_at.asc().nulls_first())
            .limit(limit)
        )
        return list((await self.session.execute(q)).scalars().all())

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

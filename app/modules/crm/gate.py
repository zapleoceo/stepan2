"""CRM read-gate — the pre-contact check that stops Stepan re-touching a lead the CRM
already moved on (manager owns it, deal closed, next step scheduled, …).

Flow: before an automated send, allow_send() consults the lead's CRM state (a cached
row refreshed by the pull sync, or refetched live when stale). A `hold` verdict stands
the lead down — bot off, stage → manager, journaled — so no more messages generate.

Fail-open by design: gate off, no phone, lead absent from CRM, or an unreachable CRM
all ALLOW the send. A CRM outage must never silence a live sales bot.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import CrmLeadState, Lead, StageEvent
from app.config import settings
from app.domain.clock import utc_now
from app.domain.enums import Stage
from app.modules.crm.service import is_safe_webhook_url
from app.modules.settings.service import get_settings

logger = logging.getLogger(__name__)


@dataclass
class CrmState:
    exists: bool
    verdict: str  # proceed | hold
    reason: str
    status: str | None
    owner: str | None
    raw: dict


class CrmReaderPort:
    async def get_state(self, url: str, secret: str, phone: str) -> dict | None: ...


# CRM fields that, when truthy, mean a human/process already owns the lead's next step —
# Stepan must stand down. Each maps to a short reason token for the journal.
_HOLD_FLAGS = {
    "deal_won": "deal won",
    "contract_signed": "contract signed",
    "paid": "paid",
    "open_task": "open task",
    "manager_called": "manager called",
    "next_contact_at": "next contact scheduled",
}


def compute_verdict(raw: dict) -> tuple[str, str]:
    """Derive proceed/hold from raw CRM fields. If the CRM already returns a `verdict`,
    trust it; otherwise apply the stand-down rule (any ownership/close/next-step signal
    → hold)."""
    explicit = str(raw.get("verdict") or "").lower()
    if explicit in ("proceed", "hold"):
        return explicit, str(raw.get("reason") or explicit)
    reasons: list[str] = []
    if str(raw.get("owner") or "").lower() == "manager":
        reasons.append("manager owns")
    reasons += [label for key, label in _HOLD_FLAGS.items() if raw.get(key)]
    return ("hold", "; ".join(reasons)) if reasons else ("proceed", "")


def _parse(raw: dict) -> CrmState:
    verdict, reason = compute_verdict(raw)
    return CrmState(
        exists=bool(raw.get("exists", True)),
        verdict=verdict, reason=reason,
        status=raw.get("status"), owner=raw.get("owner"), raw=raw,
    )


class CrmGate:
    """Per-branch CRM read-gate: state lookup (cache-aware) + stand-down enforcement."""

    def __init__(
        self, session: AsyncSession, branch_id: int, reader: CrmReaderPort
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.reader = reader

    async def allow_send(self, lead: Lead, source: str) -> tuple[bool, str]:
        """True → Stepan may send. Manager sends always pass (human override). A `hold`
        verdict returns False AND stands the lead down so nothing else generates."""
        cfg = await get_settings(self.session, self.branch_id)
        url = (cfg.crm_state_url or "").strip()
        if not cfg.crm_read_enabled or not url or source == "manager":
            return True, ""
        if not lead.phone_e164:
            return True, "no phone"
        if not is_safe_webhook_url(url):
            logger.warning("crm gate branch=%d: unsafe crm_state_url refused", self.branch_id)
            return True, "unsafe url"
        state = await self._state_for(lead, cfg.crm_read_secret, url)
        if state is None or not state.exists or state.verdict != "hold":
            return True, ""
        await self._stand_down(lead, state.reason)
        return False, state.reason

    async def refresh(self, lead: Lead) -> CrmState | None:
        """Force a live fetch + cache upsert (used by the pull sync)."""
        cfg = await get_settings(self.session, self.branch_id)
        url = (cfg.crm_state_url or "").strip()
        if not cfg.crm_read_enabled or not url or not lead.phone_e164:
            return None
        if not is_safe_webhook_url(url):
            return None
        return await self._fetch(lead, cfg.crm_read_secret, url)

    async def enforce(self, lead: Lead) -> str:
        """Pull-sync path: refresh the lead's CRM state and stand it down if `hold`.
        Returns the verdict ('proceed' | 'hold' | 'unknown' when the CRM is unreachable)."""
        state = await self.refresh(lead)
        if state is None:
            return "unknown"
        if state.exists and state.verdict == "hold":
            await self._stand_down(lead, state.reason)
        return state.verdict

    async def _state_for(self, lead: Lead, secret: str, url: str) -> CrmState | None:
        cached = await self._cached(lead.id)
        if cached is not None:
            age = utc_now() - cached.fetched_at
            if age < timedelta(seconds=settings().crm_state_ttl_s):
                return _parse(json.loads(cached.raw)) if cached.raw else _from_row(cached)
        return await self._fetch(lead, secret, url)

    async def _fetch(self, lead: Lead, secret: str, url: str) -> CrmState | None:
        raw = await self.reader.get_state(url, secret, lead.phone_e164 or "")
        if raw is None:  # CRM unreachable — keep any cached row, report no opinion
            return None
        state = _parse(raw)
        await self._upsert(lead.id, state)
        return state

    async def _cached(self, lead_id: int | None) -> CrmLeadState | None:
        if lead_id is None:
            return None
        return (await self.session.execute(
            select(CrmLeadState).where(CrmLeadState.lead_id == lead_id)
        )).scalars().first()

    async def _upsert(self, lead_id: int | None, state: CrmState) -> None:
        if lead_id is None:
            return
        row = await self._cached(lead_id)
        if row is None:
            row = CrmLeadState(branch_id=self.branch_id, lead_id=lead_id)
        row.exists_in_crm = state.exists
        row.status, row.owner = state.status, state.owner
        row.verdict, row.reason = state.verdict, state.reason
        row.raw = json.dumps(state.raw, ensure_ascii=False)
        row.fetched_at = utc_now()
        self.session.add(row)
        await self.session.flush()

    async def _stand_down(self, lead: Lead, reason: str) -> None:
        """CRM says a human/process owns this lead: silence the bot and hand off."""
        if lead.stage != Stage.MANAGER:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=None,
                from_stage=str(lead.stage), to_stage=str(Stage.MANAGER),
                actor="crm", reason=f"crm hold: {reason}" if reason else "crm hold",
            ))
            lead.stage = Stage.MANAGER
        lead.agent_enabled = False
        self.session.add(lead)
        await self.session.flush()
        logger.info("branch=%d lead=%d CRM stand-down: %s", self.branch_id, lead.id, reason)


def _from_row(row: CrmLeadState) -> CrmState:
    return CrmState(exists=row.exists_in_crm, verdict=row.verdict, reason=row.reason or "",
                    status=row.status, owner=row.owner, raw={})

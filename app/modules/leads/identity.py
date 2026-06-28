"""IdentityService — resolve a lead + channel thread, merging across channels by phone.

The no-duplicate rule: same phone in the same branch = same lead, even when the
contact arrives via a different channel. Isolation lives in the BranchScoped repos.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Lead

from .repository import LeadRepo, ThreadRepo


class IdentityService:
    """Identity resolution for one branch — leads merged by phone, threads upserted."""

    def __init__(self, session: AsyncSession, branch_id: int) -> None:
        self.session = session
        self.branch_id = branch_id
        self.leads = LeadRepo(session, branch_id)
        self.threads = ThreadRepo(session, branch_id)

    async def resolve_or_create(
        self,
        external_thread_id: str,
        channel_id: int,
        display_name: str | None,
        phone: str | None,
    ) -> tuple[Lead, ChannelThread]:
        """Return (lead, thread): phone-match → existing thread's lead → new lead."""
        thread = await self.threads.by_external(channel_id, external_thread_id)
        lead = await self._resolve_lead(thread, phone, display_name)
        thread = await self._upsert_thread(thread, lead, channel_id, external_thread_id)
        return lead, thread

    async def _resolve_lead(
        self, thread: ChannelThread | None, phone: str | None, display_name: str | None
    ) -> Lead:
        if phone:
            existing = await self.leads.by_phone(phone)
            if existing is not None:
                return existing
        if thread is not None:
            lead = await self.leads.get(thread.lead_id)
            if lead is not None:
                if phone and lead.phone_e164 is None:
                    lead.phone_e164 = phone  # backfill once the number surfaces
                return lead
        return await self.leads.add(
            Lead(display_name=display_name, phone_e164=phone, branch_id=self.branch_id)
        )

    async def _upsert_thread(
        self,
        thread: ChannelThread | None,
        lead: Lead,
        channel_id: int,
        external_thread_id: str,
    ) -> ChannelThread:
        if thread is not None:
            thread.lead_id = lead.id  # type: ignore[assignment] — may re-point on merge
            return thread
        return await self.threads.add(
            ChannelThread(
                lead_id=lead.id,  # type: ignore[arg-type]
                channel_id=channel_id,
                external_thread_id=external_thread_id,
            )
        )

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
        ig_user_id: str | None = None,
        ig_username: str | None = None,
        avatar_url: str | None = None,
    ) -> tuple[Lead, ChannelThread]:
        """Return (lead, thread): phone-match → existing thread's lead → new lead."""
        thread = await self.threads.by_external(channel_id, external_thread_id)
        lead = await self._resolve_lead(
            thread, phone, display_name, ig_user_id, ig_username, avatar_url
        )
        thread = await self._upsert_thread(thread, lead, channel_id, external_thread_id)
        return lead, thread

    async def _resolve_lead(
        self,
        thread: ChannelThread | None,
        phone: str | None,
        display_name: str | None,
        ig_user_id: str | None = None,
        ig_username: str | None = None,
        avatar_url: str | None = None,
    ) -> Lead:
        # An EXISTING thread's identity wins over a phone. The phone is mined from free
        # message text (see ingest.extract_phone) — a lead who types SOMEONE ELSE'S number
        # used to re-point their live conversation onto that number's owner (a hijack /
        # data-loss path). So phone-match merge only runs for a BRAND-NEW thread (genuine
        # first contact, the intended cross-channel merge); on an existing thread we keep
        # the thread's own lead and only backfill its empty phone.
        if thread is not None:
            lead = await self.leads.get(thread.lead_id)
            if lead is not None:
                self._backfill(lead, phone, display_name, ig_user_id, ig_username, avatar_url)
                return lead
        if phone:
            existing = await self.leads.by_phone(phone)
            if existing is not None:
                self._backfill(existing, phone, display_name, ig_user_id, ig_username, avatar_url)
                return existing
        return await self.leads.add(
            Lead(
                display_name=display_name,
                phone_e164=phone,
                ig_user_id=ig_user_id,
                ig_username=ig_username,
                avatar_url=avatar_url,
                branch_id=self.branch_id,
            )
        )

    @staticmethod
    def _backfill(
        lead: Lead,
        phone: str | None,
        display_name: str | None,
        ig_user_id: str | None,
        ig_username: str | None,
        avatar_url: str | None,
    ) -> None:
        if phone and lead.phone_e164 is None:
            lead.phone_e164 = phone
        if display_name and lead.display_name is None:
            lead.display_name = display_name
        if ig_user_id and lead.ig_user_id is None:
            lead.ig_user_id = ig_user_id
        if ig_username and lead.ig_username is None:
            lead.ig_username = ig_username
        if avatar_url:
            lead.avatar_url = avatar_url  # always refresh (CDN URL expires)

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

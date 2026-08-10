"""IdentityService — resolve a lead + channel thread, merging across channels by phone.

The no-duplicate rule: same phone in the same branch = same lead, even when the
contact arrives via a different channel. Isolation lives in the BranchScoped repos.
"""
from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Lead

from .repository import LeadRepo, ThreadRepo


class Resolved(NamedTuple):
    """Who this message belongs to, and whether the connector is new to them."""

    lead: Lead
    thread: ChannelThread
    thread_created: bool


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
        first_seen: datetime | None = None,
    ) -> Resolved:
        """Return (lead, thread, thread_created): phone-match → existing thread's lead → new.

        `thread_created` is the lead's FIRST message on this connector, and the caller acts
        on it: a first message arriving on a read-only connector means a manager already owns
        this person. Only the resolver can answer it without a second lookup — inferring it
        downstream costs a query per message on a table already scanned half a million times.

        `first_seen` is when the MESSAGE happened, and it becomes the lead's created_at.
        Defaulting to "now" was invisible while every message arrived live; a history
        backfill then stamped 306 leads with the hour it ran, and every report keyed on
        arrival date showed a spike on the day of the import instead of the months the
        conversations actually spanned."""
        thread = await self.threads.by_external(channel_id, external_thread_id)
        existed = thread is not None
        lead = await self._resolve_lead(
            thread, phone, display_name, ig_user_id, ig_username, avatar_url, first_seen
        )
        thread = await self._upsert_thread(thread, lead, channel_id, external_thread_id)
        return Resolved(lead, thread, thread_created=not existed)

    async def _resolve_lead(
        self,
        thread: ChannelThread | None,
        phone: str | None,
        display_name: str | None,
        ig_user_id: str | None = None,
        ig_username: str | None = None,
        avatar_url: str | None = None,
        first_seen: datetime | None = None,
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
                self.backfill(lead, phone, display_name, ig_user_id, ig_username, avatar_url)
                return lead
        if phone:
            existing = await self.leads.by_phone(phone)
            if existing is not None:
                self.backfill(existing, phone, display_name, ig_user_id, ig_username, avatar_url)
                return existing
        fresh = Lead(
            display_name=display_name,
            phone_e164=phone,
            ig_user_id=ig_user_id,
            ig_username=ig_username,
            avatar_url=avatar_url,
            branch_id=self.branch_id,
        )
        if first_seen is not None:
            fresh.created_at = first_seen
        return await self.leads.add(fresh)

    @staticmethod
    def backfill(
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

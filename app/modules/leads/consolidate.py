"""Two threads, one person: fold a lead into the one that shares their phone number.

The funnel used to end at the phone. Stepan takes a number, hands it to a manager, and
everything after that happened somewhere we could not see — so a lead who bought looked
identical to one who vanished.

Now the managers' WhatsApp numbers are read, and the same person exists twice: once as the
Instagram thread Stepan worked, once as the WhatsApp chat the manager worked. The phone is
what says they are the same person, and it arrives late — Stepan asks for it mid-conversation,
so the two records are already separate by the time we can tell.

Merging is therefore retroactive by nature, and it is decided here rather than at the moment
a thread is created (see IdentityService, which deliberately refuses to merge an EXISTING
thread on a phone scraped from message text — a lead who types someone else's number would
otherwise swallow that person's history).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import ChannelThread, Lead
from app.domain.enums import HUMAN_LED_STAGES, Stage

logger = logging.getLogger(__name__)


async def _threads_of(session: AsyncSession, lead_id: int) -> list[ChannelThread]:
    rows = await session.exec(
        select(ChannelThread).where(ChannelThread.lead_id == lead_id)
    )
    return list(rows.scalars().all())


def _pick_survivor(a: Lead, b: Lead) -> tuple[Lead, Lead]:
    """The lead Stepan worked survives; the manager's copy is folded into it.

    Not "the older one": the WhatsApp side is usually older (the manager's chat history
    predates the ad that produced the lead), and letting it win would move a live funnel
    record onto a contact that was never in the funnel."""
    return (a, b) if (a.stage or "") != Stage.MANAGER else (b, a)


async def merge_by_phone(session: AsyncSession, lead: Lead) -> Lead | None:
    """Fold any other lead sharing this one's phone into it. Returns the survivor, or None.

    Called after identity backfill, when a phone has just become known."""
    if not lead.phone_e164 or lead.id is None:
        return None
    rows = await session.exec(
        select(Lead).where(
            Lead.branch_id == lead.branch_id,
            Lead.phone_e164 == lead.phone_e164,
            Lead.id != lead.id,
        )
    )
    others = list(rows.scalars().all())
    if not others:
        return None

    survivor = lead
    for other in others:
        survivor, absorbed = _pick_survivor(survivor, other)
        await _absorb(session, survivor, absorbed)
    return survivor


# Rows that describe the PERSON and must follow them, not stay with the retired record.
#
# crm_lead_state is the one that bites: a booking on an absorbed lead is the same booking
# twice. The funnel filters merged leads so its totals stayed right, but any question asked
# straight of the CRM table — "how many are registered for the event" — counted a person and
# their own duplicate. Found while reconciling nine bookings against six reminders.
_FOLLOWS_THE_PERSON = ("crm_lead_state", "stage_event", "manager_alert",
                       "lead_need_tag", "need_lead_state")


async def _absorb(session: AsyncSession, survivor: Lead, absorbed: Lead) -> None:
    """Re-point everything about the absorbed lead and retire the row."""
    if survivor.id is None or absorbed.id is None or survivor.id == absorbed.id:
        return
    for thread in await _threads_of(session, absorbed.id):
        thread.lead_id = survivor.id
        session.add(thread)
    for table in _FOLLOWS_THE_PERSON:
        await session.execute(
            text(f"UPDATE {table} SET lead_id = :to WHERE lead_id = :from"),  # noqa: S608
            {"to": survivor.id, "from": absorbed.id},
        )
    # Keep whatever the absorbed record knew that the survivor does not. A manager's contact
    # often carries the only real name we have.
    if not survivor.display_name and absorbed.display_name:
        survivor.display_name = absorbed.display_name
    if not survivor.avatar_url and absorbed.avatar_url:
        survivor.avatar_url = absorbed.avatar_url

    await _silence_bot(session, survivor)
    absorbed.is_merged_into = survivor.id
    absorbed.agent_enabled = False
    session.add(absorbed)
    session.add(survivor)
    logger.info("merged lead %s into %s on phone %s",
                absorbed.id, survivor.id, survivor.phone_e164)


async def _silence_bot(session: AsyncSession, lead: Lead) -> None:
    """A phone match means a human already has this person.

    The manager is mid-conversation on WhatsApp; a bot still working the Instagram thread
    would give the same customer two parallel conversations from one school, contradicting
    each other on price. Same treatment as a manual hand-off, and reversible the same way."""
    if (lead.stage or "") in HUMAN_LED_STAGES:
        return
    lead.stage = Stage.HANDED_OFF
    lead.agent_enabled = False
    lead.handed_off_at = getattr(lead, "handed_off_at", None) or datetime.now(UTC).replace(
        tzinfo=None)
    session.add(lead)


async def sweep(session: AsyncSession, branch_id: int) -> int:
    """Merge every phone that currently points at more than one lead. Returns merges done.

    A sweep rather than only an on-ingest hook: phones arrived before this code existed, and
    a lead can also get one from the CRM or from an operator typing it in."""
    rows = await session.exec(
        select(Lead).where(
            Lead.branch_id == branch_id,
            Lead.phone_e164.is_not(None),  # type: ignore[union-attr]
            Lead.is_merged_into.is_(None),  # type: ignore[union-attr]
        )
    )
    by_phone: dict[str, list[Lead]] = {}
    for lead in rows.scalars().all():
        by_phone.setdefault(str(lead.phone_e164), []).append(lead)

    merged = 0
    for leads in by_phone.values():
        if len(leads) < 2:
            continue
        survivor = leads[0]
        for other in leads[1:]:
            survivor, absorbed = _pick_survivor(survivor, other)
            await _absorb(session, survivor, absorbed)
            merged += 1
    return merged

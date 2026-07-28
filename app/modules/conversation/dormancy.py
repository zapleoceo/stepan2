"""Parking a lead as dormant — one implementation, five call sites.

The move was written out longhand in delivery (hard stop, non-target), followup (schedule
exhausted, and again when a nudge came back dry) and outbox (an explicit stop, and the last
send of the schedule). Every copy had to remember the same four things: skip if already
dormant, journal the transition with the stage it came FROM, set the stage, and switch the bot
off so the agent flag and the funnel never disagree. Four of the five also had to remember not
to touch a lead a human has taken over.

They agreed, which is the good case and also why nobody noticed the shape: the next reason to
park someone would have been a sixth copy, and the first one to forget a line would have left
a lead marked dormant with the bot still on — silently answering someone we had recorded as
given up on.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Lead, StageEvent
from app.domain.enums import HUMAN_LED_STAGES, Stage


async def park_dormant(
    session: AsyncSession, branch_id: int, lead: Lead | None, thread_id: int | None,
    *, actor: str, reason: str, created_at: object = None,
    respect_human_led: bool = True,
) -> bool:
    """Mark the lead dormant and silence the bot. True when this call did it.

    `respect_human_led` leaves a lead a manager has taken over exactly where the manager put
    them — the bot must never quietly reclaim a hand-off. The one caller that passes False is
    the hard-stop path: an explicit "stop contacting me" outranks whose lead it is."""
    if lead is None or lead.stage == Stage.DORMANT:
        return False
    if respect_human_led and lead.stage in HUMAN_LED_STAGES:
        return False
    event = StageEvent(
        branch_id=branch_id, lead_id=lead.id, thread_id=thread_id,
        from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
        actor=actor, reason=reason,
    )
    if created_at is not None:
        event.created_at = created_at  # type: ignore[assignment]
    session.add(event)
    lead.stage = Stage.DORMANT
    # The agent flag and the funnel stage must never disagree: a dormant lead whose bot is
    # still on keeps answering someone we have recorded as given up on.
    lead.agent_enabled = False
    session.add(lead)
    return True

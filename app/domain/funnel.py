"""What a stage change means for the bot switch — one rule, every writer.

The pairing is not incidental: a stage says who owns the conversation, and the bot switch
enforces it. Writing them apart is how they drift, and a lead whose stage says "the manager
has this" while the bot still answers is two voices from one school.

Pure: no session, no models, no I/O. The Protocol is the whole coupling to the ORM.
"""
from __future__ import annotations

from typing import Protocol

from app.domain.enums import BOT_SILENT_STAGES, Stage


class StageOwner(Protocol):
    """The three fields a stage move touches. Lead satisfies it; so does a test double."""

    stage: Stage
    agent_enabled: bool
    agent_off_manual: bool


def apply_stage(lead: StageOwner, target: Stage) -> None:
    """Move the lead to `target` and put the bot switch where that stage requires.

    MANAGER is a human takeover: the bot goes quiet, but the stage is deliberately NOT in
    BOT_SILENT_STAGES — silence there is the switch, not the stage, which is exactly what
    lets a manager hand the thread back under supervision.

    Any active messaging stage re-arms the bot AND clears a manual mute, because moving a
    lead back into the funnel IS handing the thread to the bot; leaving the Bot OFF pill set
    would make the move look applied while nothing answered.

    READY, HANDED_OFF and DORMANT are left alone on purpose: they are bot-silent by stage, so
    the switch carries no meaning there and forcing it would overwrite a human's decision.
    """
    lead.stage = target
    if target == Stage.MANAGER:
        lead.agent_enabled = False
    elif target not in BOT_SILENT_STAGES:
        lead.agent_enabled = True
        lead.agent_off_manual = False

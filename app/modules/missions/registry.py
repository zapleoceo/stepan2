"""The missions Stepan runs, and which connectors can carry them.

Two of these describe work that already ships — they are declarations of what the code does
today, not new behaviour. Declaring them first is what makes the next two additions rather
than rewrites, and it is the same move that worked for connectors: describe the three that
exist, then adding a fourth is a file instead of edits in ten places.
"""
from __future__ import annotations

from app.connectors.spec import Capability

from .spec import Grounding, Initiative, MissionSpec

INBOUND_REPLY = MissionSpec(
    key="inbound_reply",
    label="Ответ в личке",
    initiative=Initiative.REACTIVE,
    grounding=Grounding.NORMAL,
    requires=frozenset(),  # every connector can read and send; that is the baseline
    goal="контакт: имя и номер, дальше менеджер",
    # The overwhelming majority of the account's budget, and rightly so: this is a person
    # waiting for an answer, which is the one action a platform never penalises.
    budget_share=0.75,
)

COMMENT_REPLY = MissionSpec(
    key="comment_reply",
    label="Ответ под своим постом",
    initiative=Initiative.REACTIVE,
    grounding=Grounding.STRICT,
    requires=frozenset({Capability.COMMENTS}),
    # Not a sale. The public line answers the question and invites the person into DM, where
    # INBOUND_REPLY takes over — the comment-to-DM shape the market converts best on, and the
    # only outreach the platform actively approves of.
    goal="перевод в личку, где работает inbound_reply",
    budget_share=0.15,
)

# Everything registered. Proactive missions are deliberately absent until the reactive pair
# is running under one budget — adding them first would mean tuning three counters at once on
# the account that carries the entire Jakarta funnel.
ALL: tuple[MissionSpec, ...] = (INBOUND_REPLY, COMMENT_REPLY)

BY_KEY: dict[str, MissionSpec] = {m.key: m for m in ALL}


def missions_for(capabilities: frozenset[Capability]) -> tuple[MissionSpec, ...]:
    """The missions a connector with these capabilities can actually run."""
    return tuple(m for m in ALL if m.runs_on(capabilities))


def mission(key: str) -> MissionSpec:
    try:
        return BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"unknown mission {key!r}; registered: {sorted(BY_KEY)}") from exc

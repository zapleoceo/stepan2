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

PROACTIVE_COMMENT = MissionSpec(
    key="proactive_comment",
    label="Комментарий под чужим постом",
    initiative=Initiative.PROACTIVE,
    grounding=Grounding.STRICT,
    requires=frozenset({Capability.OUTBOUND_COMMENT}),
    # Not selling, and not even inviting: a line under the post of somebody who already wrote
    # to us once, about the thing they actually posted. What it buys is being seen again by a
    # person who knows us — a reply, if it comes, lands in DM where INBOUND_REPLY works.
    goal="напомнить о себе тем, кто уже писал — без продажи",
    # The smallest share of the three, deliberately. It is the only mission that speaks
    # uninvited, which is the exact shape platform anti-spam exists to catch, and the account
    # carrying the whole Jakarta funnel is not where anyone should find the line.
    budget_share=0.10,
)

# Everything registered. The reactive pair carries the funnel; the proactive one runs on what
# is left, and only where a connector declares it can write into somebody else's space.
ALL: tuple[MissionSpec, ...] = (INBOUND_REPLY, COMMENT_REPLY, PROACTIVE_COMMENT)

BY_KEY: dict[str, MissionSpec] = {m.key: m for m in ALL}


def missions_for(capabilities: frozenset[Capability]) -> tuple[MissionSpec, ...]:
    """The missions a connector with these capabilities can actually run."""
    return tuple(m for m in ALL if m.runs_on(capabilities))


def mission(key: str) -> MissionSpec:
    try:
        return BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"unknown mission {key!r}; registered: {sorted(BY_KEY)}") from exc

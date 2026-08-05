"""What a mission IS — the job Stepan is doing, as data.

A connector answers WHERE and HOW: transport, credentials, what the platform allows. A
mission answers WHY: who we are writing to, how strictly the draft is checked, at what pace,
and what counts as success. One connector runs several missions — Instagram carries the DM
replies, the public comment replies, and (soon) proactive outreach — and they agree on almost
nothing that matters.

Until now each job hard-coded its own answers. Replies live in conversation/, comments in
comments/, and each re-decided the same four questions in its own words. A fifth job would
have been a third module repeating them a third time.

The one thing missions must NOT own is the pace. See budget.py: Instagram counts actions per
ACCOUNT, and three missions each obeying its own cap is how an account gets throttled while
every individual limit looks respected.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.connectors.spec import Capability


class Initiative(StrEnum):
    """Who spoke first — the single biggest driver of platform risk.

    A platform tolerates an account that answers people who wrote to it. The same volume of
    unprompted messages into other people's space is the shape every anti-spam system is
    built to catch, and it is punished with a shadowban: posts stay visible to followers and
    quietly vanish from hashtags and Explore, with no notification."""

    REACTIVE = "reactive"    # they wrote first — a DM, a comment under our own post
    PROACTIVE = "proactive"  # we went to them — their post, their space


class Grounding(StrEnum):
    """How much doubt is allowed before we say nothing.

    Not a style preference. A wrong price in a DM is a conversation to fix; a wrong price
    under a public post is a screenshot, and it stays there."""

    NORMAL = "normal"   # private: answer, and correct if it drifts
    STRICT = "strict"   # public: only what the knowledge base states, or say nothing


@dataclass(frozen=True)
class MissionSpec:
    """One job, declared rather than implied.

    `requires` is checked against the connector's declared capabilities, so a mission simply
    does not exist on a connector that cannot carry it — the website has no way to comment and
    no way to write first, and that falls out of the declaration instead of a branch id check.
    """

    key: str
    label: str
    initiative: Initiative
    grounding: Grounding
    requires: frozenset[Capability]
    # What the mission is FOR, in the funnel's terms. Named so a report can group by it and
    # so nobody has to infer intent from the code: an outreach comment that never captures a
    # phone is not failing, it was never trying to.
    goal: str
    # Share of the connector's action budget this mission may take. They sum to <= 1.0 per
    # connector — the budget itself is per ACCOUNT, and dividing it is the only honest way to
    # let several missions run without racing each other into a throttle.
    budget_share: float

    def runs_on(self, capabilities: frozenset[Capability]) -> bool:
        return self.requires <= capabilities

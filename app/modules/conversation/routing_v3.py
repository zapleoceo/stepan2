"""Which model tier a v3 turn runs on — decided from state, not from text patterns.

v2 routed partly on regexes over the lead's last message ("bayar", "mahal", …), which meant a
phrasing nobody had seen yet quietly got the cheap model at the exact moment a sale was on the
line. v3 routes on the dossier and the turn index instead: both are structural, so an unseen
phrasing can't downgrade a decisive turn.

Routing is a cost decision, invisible to the lead — a wrong call costs money or a slightly
weaker sentence, never a wrong claim. The quality gate is a separate concern (see guard_v3).
"""
from __future__ import annotations

from .dossier import LeadDossier
from .routing import FAST, SMART

__all__ = ["FAST", "SMART", "pick_capability_v3"]


def pick_capability_v3(dossier: LeadDossier, *, is_first_reply: bool) -> str:
    """The strong model whenever this turn can plausibly cost the sale, else the cheap one.

    The opener is included because it is the single highest-stakes turn in the funnel: 65% of
    this branch's leads never write a third message, so a weak first reply is most of the loss.
    Everything else keys off the dossier — an unresolved objection, a live money conversation,
    a lead who is weighing it up, or one who has started saying no."""
    if is_first_reply:
        return SMART
    if dossier.open_objections():
        return SMART
    if dossier.prices_quoted or dossier.payment_preference or dossier.budget_signal:
        return SMART
    if dossier.readiness in ("considering", "ready"):
        return SMART
    if dossier.refusal != "none":
        return SMART
    return FAST

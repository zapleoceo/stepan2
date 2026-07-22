"""Which model tier a turn runs on — decided from state, not from text patterns.

The previous router keyed partly off regexes over the lead's last message ("bayar", "mahal",
…), so a phrasing nobody had seen yet quietly got the cheap model at the exact moment a sale
was on the line. This one reads the dossier and the turn index: both structural, so an unseen
phrasing cannot downgrade a decisive turn.

Routing is a cost decision, invisible to the lead — a wrong call costs money or a slightly
weaker sentence, never a wrong claim. Quality is a separate concern (money_gate, critic).
"""
from __future__ import annotations

from .dossier import LeadDossier

SMART = "chat:smart"  # the strong, scarce model
FAST = "chat:fast"    # the cheap, effectively unlimited one

__all__ = ["FAST", "SMART", "pick_capability"]


def pick_capability(dossier: LeadDossier, *, is_first_reply: bool) -> str:
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

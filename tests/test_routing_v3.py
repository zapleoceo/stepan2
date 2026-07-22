"""v3 tier routing — decided from state, never from text patterns.

v2 partly routed on regexes over the lead's last message, so a phrasing nobody had seen yet
got the cheap model at the exact moment a sale was on the line. Everything here keys off the
dossier or the turn index, both structural.
"""
from __future__ import annotations

from app.modules.conversation.dossier import LeadDossier, Objection
from app.modules.conversation.routing_v3 import FAST, SMART, pick_capability_v3


def _pick(dossier: LeadDossier, first: bool = False) -> str:
    return pick_capability_v3(dossier, is_first_reply=first)


def test_the_opener_always_gets_the_strong_model() -> None:
    """65% of this branch's leads never write a third message — the opener is most of the loss."""
    assert _pick(LeadDossier(), first=True) == SMART


def test_an_ordinary_mid_conversation_turn_runs_cheap() -> None:
    assert _pick(LeadDossier(role="student", readiness="exploring")) == FAST


def test_an_unresolved_objection_forces_the_strong_model() -> None:
    assert _pick(LeadDossier(objections=[Objection("mahal")])) == SMART


def test_an_objection_already_handled_does_not_keep_paying_for_smart() -> None:
    assert _pick(LeadDossier(objections=[Objection("mahal", "handled", "cicilan")])) == FAST


def test_a_live_money_conversation_forces_the_strong_model() -> None:
    assert _pick(LeadDossier(prices_quoted=["DP 500rb"])) == SMART
    assert _pick(LeadDossier(payment_preference="cicilan")) == SMART
    assert _pick(LeadDossier(budget_signal="lagi tipis")) == SMART


def test_a_lead_weighing_it_up_or_ready_forces_the_strong_model() -> None:
    assert _pick(LeadDossier(readiness="considering")) == SMART
    assert _pick(LeadDossier(readiness="ready")) == SMART


def test_any_degree_of_refusal_forces_the_strong_model() -> None:
    """Soft, vague and blunt each need a different reaction — the cheap model conflates them."""
    for degree in ("soft", "vague", "blunt"):
        assert _pick(LeadDossier(refusal=degree)) == SMART


def test_routing_never_reads_the_lead_message_text() -> None:
    """The signature takes no text at all — an unseen phrasing structurally cannot downgrade
    a decisive turn, which is the v2 failure this replaces."""
    import inspect
    params = set(inspect.signature(pick_capability_v3).parameters)
    assert params == {"dossier", "is_first_reply"}

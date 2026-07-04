"""Funnel guardrails baked into the decision prompt — regression cover so a future edit
can't silently drop the anti-repeat / buying-signal / one-product / soft-qualify rules
(added after a 20-transcript review found the funnel stalling on qualifying)."""
from __future__ import annotations

from app.modules.conversation.prompt import _DECISION_CONTRACT


def test_anti_repeat_builds_on_partial_answer() -> None:
    # lead already answered → advance, don't re-ask reworded
    assert "IF THE LEAD ALREADY ANSWERED" in _DECISION_CONTRACT
    assert "narrowing follow-up" in _DECISION_CONTRACT


def test_enroll_signal_collects_contact_before_format() -> None:
    assert "ENROLL / PAYMENT REFLEX" in _DECISION_CONTRACT
    assert "COLLECT THE CONTACT FIRST" in _DECISION_CONTRACT


def test_one_product_facts_no_mixing() -> None:
    assert "ONE PRODUCT'S FACTS ONLY" in _DECISION_CONTRACT


def test_soft_qualify_gate_present() -> None:
    assert "SOFT-QUALIFY EARLY" in _DECISION_CONTRACT
    assert "risk signal" in _DECISION_CONTRACT.lower()

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


def test_catch_all_answer_is_narrowed_not_reasked() -> None:
    assert "CATCH-ALL ANSWERS" in _DECISION_CONTRACT


def test_capture_contact_early_but_not_ready() -> None:
    assert "CAPTURE CONTACT EARLY" in _DECISION_CONTRACT
    # a WhatsApp shared for materials must NOT flip the lead to ready/handoff
    assert "is NOT 'ready'" in _DECISION_CONTRACT
    assert "AND wants to ENROL" in _DECISION_CONTRACT


def test_proactive_close_and_openhouse_bridge() -> None:
    assert "PROACTIVELY CLOSE" in _DECISION_CONTRACT
    assert "OPEN HOUSE" in _DECISION_CONTRACT


def test_events_vs_courses_recognizes_cheap_price() -> None:
    assert "EVENTS vs COURSES" in _DECISION_CONTRACT
    assert "kirain 100k" in _DECISION_CONTRACT


def test_phone_before_handoff_rule_and_field() -> None:
    assert "PHONE BEFORE HAND-OFF" in _DECISION_CONTRACT
    assert '"phone"' in _DECISION_CONTRACT


def test_decision_parses_phone() -> None:
    from app.modules.conversation.decision import parse_decision
    d = parse_decision('{"reply":"hi","stage":"presenting","phone":"0812345"}')
    assert d.phone == "0812345"
    d2 = parse_decision('{"reply":"hi","stage":"presenting"}')
    assert d2.phone is None


def test_students_are_a_target_segment() -> None:
    assert "STUDENTS (school-age) ARE A TARGET" in _DECISION_CONTRACT
    assert "10% student discount" in _DECISION_CONTRACT
    assert "a parent pays" in _DECISION_CONTRACT


def test_no_invented_proof_or_cross_product_trial() -> None:
    assert "DON'T OFFER WHAT YOU CAN'T DELIVER" in _DECISION_CONTRACT
    assert "no invented alumni success stories" in _DECISION_CONTRACT
    assert "NO Vibe Coding Skill Booster" in _DECISION_CONTRACT

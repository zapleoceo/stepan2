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
    assert "genuine dead end" in _DECISION_CONTRACT.lower()  # soft-close reserved for these


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


def test_stage_reason_rule_and_field_present() -> None:
    assert "stage_reason" in _DECISION_CONTRACT
    assert '"stage_reason"' in _DECISION_CONTRACT


def test_decision_parses_stage_reason() -> None:
    from app.modules.conversation.decision import parse_decision
    d = parse_decision(
        '{"reply":"hi","stage":"presenting","stage_reason":"лид назвал боль"}')
    assert d.stage_reason == "лид назвал боль"
    d2 = parse_decision('{"reply":"hi","stage":"presenting"}')
    assert d2.stage_reason is None


def test_students_are_a_target_segment() -> None:
    assert "STUDENTS (school-age) ARE A TARGET" in _DECISION_CONTRACT
    assert "10% student discount" in _DECISION_CONTRACT
    assert "a parent pays" in _DECISION_CONTRACT


def test_no_invented_proof_or_cross_product_trial() -> None:
    assert "DON'T OFFER WHAT YOU CAN'T DELIVER" in _DECISION_CONTRACT
    assert "no invented alumni success stories" in _DECISION_CONTRACT
    assert "NO Vibe Coding Skill Booster" in _DECISION_CONTRACT


def test_early_adult_vs_student_split() -> None:
    assert "split ADULT vs SCHOOL-AGE early" in _DECISION_CONTRACT
    assert "NEVER soft-close someone just for being a student" in _DECISION_CONTRACT


def test_compound_question_gets_every_part_answered() -> None:
    # thread 2159, 2026-07-08: "price list and the syllabus" got only the price answered,
    # lead had to chase the rest ("btw syllabus nya td gmn ya")
    assert "TWO OR MORE ASKS" in _DECISION_CONTRACT
    assert "EVERY part answered" in _DECISION_CONTRACT


def test_plain_acknowledgment_never_needs_manager() -> None:
    # threads 2324/2337/2272/2403, 2026-07-09/10: "boleh min" / "Minat ka" /
    # "Thanks untuk infonya" / "kayanya mau serius jadi spesialis SMM" all got escalated to
    # a human with nothing to actually resolve — judge INTENT, not exact wording
    assert "ANY POSITIVE, AGREEING OR READY SIGNAL" in _DECISION_CONTRACT
    assert "Judge the INTENT" in _DECISION_CONTRACT
    assert "NEVER for a lead simply agreeing" in _DECISION_CONTRACT


def test_undecipherable_slang_is_non_target_not_needs_manager() -> None:
    # thread 2397, 2026-07-09: PUBG gaming slang ("main epep", "ratain satu squad di bermuda")
    # escalated to a human who can't decode it either — should be non_target + soft close
    assert "UNDECIPHERABLE SLANG" in _DECISION_CONTRACT
    assert "non_target, NOT needs_manager" in _DECISION_CONTRACT


def test_stage_reason_required_not_optional() -> None:
    assert "REQUIRED (not optional)" in _DECISION_CONTRACT


def test_followup_contract_is_lighter_but_keeps_the_same_json_schema() -> None:
    """A follow-up nudge doesn't need the full sales-methodology teaching — only the
    JSON schema (so parse_decision/_apply_decision behave identically) and the
    anti-fabrication/escalation guardrails that still apply regardless of workflow."""
    from app.modules.conversation.prompt import _FOLLOWUP_CONTRACT, _JSON_SCHEMA_BLOCK

    assert len(_FOLLOWUP_CONTRACT) < len(_DECISION_CONTRACT) / 2
    assert _JSON_SCHEMA_BLOCK in _FOLLOWUP_CONTRACT
    assert _JSON_SCHEMA_BLOCK in _DECISION_CONTRACT
    # the essential guardrails still carry over
    assert "NEVER FABRICATE" in _FOLLOWUP_CONTRACT
    assert "ANY POSITIVE, AGREEING OR READY SIGNAL" in _FOLLOWUP_CONTRACT
    assert "PHONE BEFORE HAND-OFF" in _FOLLOWUP_CONTRACT
    assert '"stage_reason"' in _FOLLOWUP_CONTRACT


def test_build_messages_uses_the_light_contract_for_followup_workflow() -> None:
    from app.modules.conversation.prompt import (
        _FOLLOWUP_CONTRACT,
        build_messages,
    )

    live = build_messages("kb", [], "id", workflow="reply")
    nudge = build_messages("kb", [], "id", workflow="followup")
    assert _FOLLOWUP_CONTRACT.format(lang="id") in nudge[0]["content"]
    assert _FOLLOWUP_CONTRACT.format(lang="id") not in live[0]["content"]

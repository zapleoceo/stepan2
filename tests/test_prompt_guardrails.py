"""Funnel guardrails baked into the decision prompt — regression cover so a future edit
can't silently drop the anti-repeat / buying-signal / one-product / objection / close rules.

The prompt was rewritten (2026-07-20) to absorb the former playbook KB docs and to strip the
thread-NNNN incident noise the model can't read; these assertions pin the NEW canonical
phrasing of each rule, so the guidance can't silently disappear even though it moved."""
from __future__ import annotations

from app.modules.conversation.prompt import _DECISION_CONTRACT


def test_anti_repeat_builds_on_partial_answer() -> None:
    assert "If the lead ALREADY answered, BUILD ON IT" in _DECISION_CONTRACT
    assert "narrowing follow-up" in _DECISION_CONTRACT


def test_enroll_signal_collects_contact_before_format() -> None:
    assert "ENROLL / PAYMENT REFLEX" in _DECISION_CONTRACT
    assert "take the contact" in _DECISION_CONTRACT
    assert "don't first ask which format/group" in _DECISION_CONTRACT


def test_one_product_facts_no_mixing() -> None:
    assert "ONE product's facts only" in _DECISION_CONTRACT


def test_handle_open_objection_before_pitching() -> None:
    assert "HANDLE A LIVE OBJECTION FIRST" in _DECISION_CONTRACT
    assert "talking over an objection" in _DECISION_CONTRACT


def test_catch_all_answer_is_narrowed_not_reasked() -> None:
    assert "vague catch-all" in _DECISION_CONTRACT
    assert "narrow it FOR" in _DECISION_CONTRACT


def test_capture_contact_early_but_not_ready() -> None:
    assert "CONTACT CAPTURE" in _DECISION_CONTRACT
    # a WhatsApp shared for materials must NOT flip the lead to ready/handoff
    assert "is NOT ready" in _DECISION_CONTRACT
    assert "AND wants to ENROL" in _DECISION_CONTRACT


def test_proactive_close_and_openhouse_bridge() -> None:
    assert "CLOSING" in _DECISION_CONTRACT
    assert "don't wait to be asked" in _DECISION_CONTRACT
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


def test_decision_parses_open_objections() -> None:
    from app.modules.conversation.decision import parse_decision
    d = parse_decision('{"reply":"hi","stage":"objection","open_objections":["mahal"]}')
    assert d.open_objections == ["mahal"]


def test_students_are_a_target_segment() -> None:
    assert "STUDENTS (school-age) ARE A TARGET" in _DECISION_CONTRACT
    assert "10% student discount" in _DECISION_CONTRACT
    assert "a parent pays" in _DECISION_CONTRACT


def test_no_invented_proof_or_cross_product_trial() -> None:
    assert "NO FABRICATION" in _DECISION_CONTRACT
    assert "NO Vibe Coding Skill Booster" in _DECISION_CONTRACT


def test_students_never_soft_closed_just_for_being_a_student() -> None:
    assert "never dismiss a student or mark them non_target" in _DECISION_CONTRACT
    assert "route toward the parent" in _DECISION_CONTRACT


def test_compound_question_gets_every_part_answered() -> None:
    assert "TWO OR MORE asks" in _DECISION_CONTRACT
    assert "EVERY part answered" in _DECISION_CONTRACT


def test_plain_acknowledgment_never_needs_manager() -> None:
    assert "ANY POSITIVE / AGREEING / READY SIGNAL" in _DECISION_CONTRACT
    assert "judge the INTENT" in _DECISION_CONTRACT
    assert "NEVER for a lead simply agreeing" in _DECISION_CONTRACT


def test_undecipherable_slang_is_non_target_not_needs_manager() -> None:
    assert "Undecipherable slang" in _DECISION_CONTRACT
    assert "non_target, not needs_manager" in _DECISION_CONTRACT


def test_lead_auto_reply_is_not_escalated() -> None:
    # situations.is_auto_reply owns this deterministically (it gates lead_spoke_own_words);
    # assert the detector, and that the prompt doesn't grow the rule back.
    from app.modules.conversation.situations import is_auto_reply

    assert is_auto_reply(
        "Halo, terima kasih telah menghubungi kami, pesan Anda akan segera kami balas")
    assert "AUTO-REPLY / AWAY MESSAGE" not in _DECISION_CONTRACT


def test_no_thread_number_noise_in_the_prompt() -> None:
    # the rewrite stripped every "thread NNNN" incident reference — they carry zero signal to
    # the model and only cost tokens. Guard against them creeping back in.
    import re
    assert not re.search(r"thread \d{3,}", _DECISION_CONTRACT)


def test_stage_reason_required_not_optional() -> None:
    assert "REQUIRED whenever" in _DECISION_CONTRACT


def test_followup_contract_is_lighter_but_keeps_the_same_json_schema() -> None:
    from app.modules.conversation.prompt import _FOLLOWUP_CONTRACT, _JSON_SCHEMA_BLOCK

    assert len(_FOLLOWUP_CONTRACT) < len(_DECISION_CONTRACT) / 2
    assert _JSON_SCHEMA_BLOCK in _FOLLOWUP_CONTRACT
    assert _JSON_SCHEMA_BLOCK in _DECISION_CONTRACT
    assert "NEVER FABRICATE" in _FOLLOWUP_CONTRACT
    assert "ANY POSITIVE, AGREEING OR READY SIGNAL" in _FOLLOWUP_CONTRACT
    assert "PHONE BEFORE HAND-OFF" in _FOLLOWUP_CONTRACT
    assert '"stage_reason"' in _FOLLOWUP_CONTRACT


def test_followup_never_re_addresses_a_handled_concern() -> None:
    from app.modules.conversation.prompt import _FOLLOWUP_CONTRACT
    assert "ADVANCES" in _FOLLOWUP_CONTRACT
    assert "ALREADY addressed" in _FOLLOWUP_CONTRACT
    assert "better to stay silent" in _FOLLOWUP_CONTRACT


def test_followup_handles_open_objection() -> None:
    from app.modules.conversation.prompt import _FOLLOWUP_CONTRACT
    assert "HANDLE A LIVE OBJECTION" in _FOLLOWUP_CONTRACT


def test_build_messages_uses_the_light_contract_for_followup_workflow() -> None:
    from app.modules.conversation.prompt import (
        _FOLLOWUP_CONTRACT,
        build_messages,
    )

    live = build_messages("kb", [], "id", workflow="reply")
    nudge = build_messages("kb", [], "id", workflow="followup")
    assert _FOLLOWUP_CONTRACT.format(lang="id") in nudge[0]["content"]
    assert _FOLLOWUP_CONTRACT.format(lang="id") not in live[0]["content"]


def test_followup_has_the_what_changed_angle() -> None:
    from app.modules.conversation.prompt import _FOLLOWUP_CONTRACT
    assert "WHAT-CHANGED ANGLE" in _FOLLOWUP_CONTRACT
    assert "never a re-pitch" in _FOLLOWUP_CONTRACT


def test_ad_opener_is_not_permission_to_present() -> None:
    assert "is a CLICK, not the lead's words" in _DECISION_CONTRACT
    assert "no presentation until a real need surfaces" in _DECISION_CONTRACT
    # a TYPED question (own words) must still be answered, not deferred
    assert "gets a REAL answer THIS turn" in _DECISION_CONTRACT


def test_money_question_leads_with_paid_not_no() -> None:
    assert "IS IT PAID" in _DECISION_CONTRACT
    assert "NEVER open with 'Tidak'" in _DECISION_CONTRACT


def test_answer_first_then_ask_for_contact() -> None:
    assert "ANSWER a real question FIRST" in _DECISION_CONTRACT
    assert "add the contact ask on top" in _DECISION_CONTRACT


def test_need_payoff_gain_is_pulled_before_presenting() -> None:
    assert "Always attempt this before presenting" in _DECISION_CONTRACT
    assert "the gain is what your pitch sells back" in _DECISION_CONTRACT


def test_nanti_procrastination_reframe_present() -> None:
    # thread 1835: 'kalau nanti saya jadi' — the postpone-the-decision objection needs an
    # honest cost-of-waiting reframe (others start first and take the projects), not pressure.
    assert "NANTI / LATER" in _DECISION_CONTRACT
    assert "COST OF WAITING" in _DECISION_CONTRACT
    assert "take the projects/clients first" in _DECISION_CONTRACT
    assert "NO fake scarcity" in _DECISION_CONTRACT


def test_no_present_on_a_single_vague_goal() -> None:
    # thread 4686: presented SMM after only 'cari pengalaman', job==gain, no pain captured.
    assert "DON'T PRESENT ON A SINGLE VAGUE GOAL" in _DECISION_CONTRACT
    assert "jobs and gains are NOT the same field" in _DECISION_CONTRACT


def test_generic_price_does_not_dump_every_total() -> None:
    # thread 4693: dumped 3 products' full totals to a generic 'berapa biaya'.
    assert "do NOT list EVERY product's full total" in _DECISION_CONTRACT

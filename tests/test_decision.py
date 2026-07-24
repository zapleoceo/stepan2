"""v3 decision parsing and the adapter back to the legacy Decision.

Guiding rule throughout: a malformed field costs that field, never the reply. The text is what
reaches the lead, and v2's habit of aborting or stubbing a turn over a contract slip is exactly
what produced "не смог ответить".
"""
from __future__ import annotations

import json

import pytest

from app.domain.enums import Stage
from app.modules.conversation.decision import TurnDecision, parse_turn_decision
from app.modules.conversation.dossier import LeadDossier, Objection


def _raw(**over) -> str:  # noqa: ANN003
    payload = {"reply": "halo kak", "move": "answer_question", "stage": "qualifying"}
    payload.update(over)
    return json.dumps(payload)


def test_parses_a_full_answer() -> None:
    d = parse_turn_decision(_raw(
        product_slug="vibe_coding", ready=True, phone="08123456789",
        needs_human=False, reply_language="id",
        dossier={"role": "student", "pains": ["takut telat"], "refusal": "none"}))
    assert d.reply == "halo kak"
    assert d.move == "answer_question"
    assert d.stage is Stage.QUALIFYING
    assert d.product_slug == "vibe_coding"
    assert d.ready is True
    assert d.phone == "08123456789"
    assert d.dossier.role == "student"
    assert d.dossier.pains == ["takut telat"]


def test_tolerates_markdown_fences() -> None:
    assert parse_turn_decision(f"```json\n{_raw()}\n```").reply == "halo kak"


@pytest.mark.parametrize("bad", ["", "not json", "[1,2]", '{"move":"close"}', '{"reply":5}'])
def test_a_broken_contract_raises_rather_than_returning_a_bad_reply(bad: str) -> None:
    with pytest.raises(ValueError, match="decision"):
        parse_turn_decision(bad)


def test_the_models_own_move_label_is_kept_sanitized() -> None:
    """The move is telemetry, not a gate input — keep the model's label, slugified."""
    assert parse_turn_decision(_raw(move="upsell_hard")).move == "upsell_hard"
    assert parse_turn_decision(_raw(move="Comfort Then Close!")).move == "comfort_then_close"
    assert parse_turn_decision(_raw(move=None)).move == "free_move"


def test_an_unknown_stage_falls_back_to_an_active_one() -> None:
    assert parse_turn_decision(_raw(stage="greeting")).stage is Stage.QUALIFYING


def test_a_malformed_dossier_costs_the_learning_not_the_reply() -> None:
    d = parse_turn_decision(_raw(dossier="oops"))
    assert d.reply == "halo kak"
    assert d.dossier == LeadDossier()


def test_objections_parse_with_status_and_as_bare_strings() -> None:
    d = parse_turn_decision(_raw(dossier={"objections": [
        {"text": "mahal", "status": "handled", "handled_by": "cicilan"}, "jauh"]}))
    assert d.dossier.objections[0] == Objection("mahal", "handled", "cicilan")
    assert d.dossier.objections[1] == Objection("jauh", "open", "")


def test_reply_language_only_survives_when_it_looks_like_a_code() -> None:
    assert parse_turn_decision(_raw(reply_language="ru")).reply_language == "ru"
    assert parse_turn_decision(_raw(reply_language="bahasa indonesia")).reply_language is None


# ── adapting to the legacy Decision the delivery pipeline already understands ──

def test_legacy_fields_come_from_the_merged_dossier_not_just_this_turn() -> None:
    d = TurnDecision(reply="ok", move="close", stage=Stage.PRESENTING)
    merged = LeadDossier(job_to_be_done="pindah karier", pains=["takut telat"],
                         desired_state=["kerja remote"], objections=[Objection("mahal")])
    legacy = d.to_legacy(merged)
    assert legacy.jobs == ["pindah karier"]
    assert legacy.pains == ["takut telat"]
    assert legacy.gains == ["kerja remote"]
    assert legacy.open_objections == ["mahal"]
    assert legacy.discovery_complete is True


def test_a_blunt_refusal_maps_to_the_legacy_hard_stop() -> None:
    """The one refusal degree that must stop outreach outright."""
    legacy = TurnDecision(reply="ok", move="accept_refusal", stage=Stage.DORMANT).to_legacy(
        LeadDossier(refusal="blunt"))
    assert legacy.hard_stop is True
    assert legacy.lead_type == "non_target"


def test_a_soft_refusal_does_not_stop_anything() -> None:
    legacy = TurnDecision(reply="ok", move="accept_refusal", stage=Stage.NURTURING).to_legacy(
        LeadDossier(refusal="soft"))
    assert legacy.hard_stop is False


def test_lead_type_is_derived_from_state_rather_than_asked_for_separately() -> None:
    def kind(**kw) -> str | None:  # noqa: ANN003
        return TurnDecision(reply="x", move="give_value", stage=Stage.QUALIFYING).to_legacy(
            LeadDossier(**kw)).lead_type

    assert kind(readiness="ready") == "hot"
    assert kind(readiness="considering") == "warm"
    assert kind(readiness="exploring") == "cold"
    assert kind(budget_signal="belum ada budget") == "no_budget"
    assert kind() is None


def test_audience_is_derived_from_the_role() -> None:
    def audience(role: str) -> str | None:
        return TurnDecision(reply="x", move="give_value", stage=Stage.QUALIFYING).to_legacy(
            LeadDossier(role=role)).audience

    assert audience("school") == "student"
    assert audience("working") == "adult"
    assert audience("parent") == "adult"
    assert audience("") is None


def test_escalation_reason_reaches_both_legacy_fields() -> None:
    legacy = TurnDecision(reply="ok", move="escalate_human", stage=Stage.MANAGER,
                        needs_human=True, human_reason="lead minta bicara dengan orang"
                        ).to_legacy(LeadDossier())
    assert legacy.needs_manager is True
    assert legacy.manager_question == "lead minta bicara dengan orang"

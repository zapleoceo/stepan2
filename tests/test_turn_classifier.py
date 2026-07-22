"""Shadow AI turn-classifier (app/modules/conversation/classifier.py) — the regex-category
mapping used to compare against the classifier's pick, and the classifier's own fail-safe
behavior. Never touches the real reply; see reply.py's nudge_classifier_shadow call site."""
from __future__ import annotations

import json

from app.modules.conversation import situations as s
from app.modules.conversation.classifier import (
    TURN_TYPES,
    classify_turn,
    regex_category_for,
)


def test_regex_category_maps_each_meaning_nudge_correctly() -> None:
    assert regex_category_for(s.OBJECTION_HANDLE_NUDGE) == "soft_no"
    assert regex_category_for(s.SOFT_NO_NUDGE) == "soft_no"
    assert regex_category_for(s.SOFT_NO_WITH_QUESTION_NUDGE) == "soft_no"
    assert regex_category_for(s.POSTPONE_NUDGE) == "postpone"
    assert regex_category_for(s.PAID_SHOCK_NUDGE) == "paid_shock"
    assert regex_category_for(s.TRUST_DOUBT_NUDGE) == "trust_doubt"
    assert regex_category_for(s.LOW_BUDGET_NUDGE) == "low_budget"
    assert regex_category_for(s.ANSWER_FIRST_TIGHT_BUDGET_NUDGE) == "low_budget"
    assert regex_category_for(s.NO_TIME_NUDGE) == "no_time"
    assert regex_category_for(s.BUYING_SIGNAL_NUDGE) == "buying_signal"


def test_regex_category_is_none_for_unrelated_nudges_and_none_input() -> None:
    # AD_OPENER_NUDGE etc. aren't one of the six meaning-categories — not a real disagreement
    assert regex_category_for(s.AD_OPENER_NUDGE) == "none"
    assert regex_category_for(None) == "none"


class _FakeLLM:
    def __init__(self, turn_type: str | None = "soft_no", raise_on_call: bool = False) -> None:
        self.turn_type = turn_type
        self.raise_on_call = raise_on_call
        self.calls = 0

    async def chat(self, messages, **kw):  # noqa: ANN001, ANN003, ANN201
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("broker 502")
        return json.dumps({"turn_type": self.turn_type}), {"model": "fast", "cost_usd": 0.0}


async def test_classify_turn_returns_the_llms_pick() -> None:
    llm = _FakeLLM(turn_type="paid_shock")
    got = await classify_turn(llm, last_txt="lah bayar juga ternyata?", branch_id=1, thread_id=1)
    assert got == "paid_shock"


async def test_classify_turn_empty_text_short_circuits_without_a_call() -> None:
    llm = _FakeLLM()
    got = await classify_turn(llm, last_txt="   ", branch_id=1, thread_id=1)
    assert got == "none" and llm.calls == 0


async def test_classify_turn_unknown_label_falls_back_to_none() -> None:
    llm = _FakeLLM(turn_type="not_a_real_category")
    got = await classify_turn(llm, last_txt="halo", branch_id=1, thread_id=1)
    assert got == "none"


async def test_classify_turn_broker_error_returns_none_never_raises() -> None:
    llm = _FakeLLM(raise_on_call=True)
    got = await classify_turn(llm, last_txt="halo", branch_id=1, thread_id=1)
    assert got is None  # shadow-only: a failure here must never break the real reply


def test_turn_types_are_all_lowercase_and_unique() -> None:
    assert len(TURN_TYPES) == len(set(TURN_TYPES))
    assert all(t == t.lower() for t in TURN_TYPES)

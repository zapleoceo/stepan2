"""Entry hints state a FACT about how the chat opened — they never issue a competing order.

The ad hint used to say "don't present the product yet … open with ONE discovery question",
which fought the contract's top rule that a question gets answered first. Two instructions in
conflict meant the same ad prefill got answered on one thread and deflected on the next (live
dry-run, branch 1, threads 4954 vs 4943) — and it is a likely root of the canned opener that
grew to 71% of first replies.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.contract import contract
from app.modules.conversation.prompt import source_hint

_ORDERS = ("don't present", "do not present", "ask what brought",
           "build rapport before", "details come after")


@pytest.mark.parametrize("source", ["ad_clicktomsg", "story"])
def test_an_entry_hint_gives_no_behavioural_order(source: str) -> None:
    hint = (source_hint(source) or "").lower()
    assert hint
    for order in _ORDERS:
        assert order not in hint, f"{source} hint still commands: {order}"


def test_the_ad_hint_says_a_tap_is_not_a_question() -> None:
    """The button's text reads like a question, but the lead never typed it — so there is
    nothing to answer, and opening with a warm question is the correct move."""
    hint = (source_hint("ad_clicktomsg") or "").lower()
    assert "nothing to answer" in hint
    assert "tapped" in hint


def test_the_ad_hint_still_blocks_inventing_facts_about_the_lead() -> None:
    """A button tap reveals a topic, never a goal, an age or a budget."""
    hint = (source_hint("ad_clicktomsg") or "").lower()
    assert "no goal" in hint and "no budget" in hint


def test_the_contract_scopes_answer_first_to_words_the_lead_typed() -> None:
    """Otherwise it collides with the ad hint, and two rules in conflict make the model pick
    one at random — which is exactly what live threads showed."""
    text = contract("id")
    assert "If the lead TYPED a question" in text
    assert "a prefilled ad button is a tap, not a question" in text


def test_an_unknown_entry_point_adds_nothing() -> None:
    """Organic leads get no assumptions at all."""
    assert source_hint(None) is None
    assert source_hint("organic") is None

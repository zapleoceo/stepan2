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

_ORDERS = ("don't present", "do not present", "open with one", "ask what brought",
           "build rapport before", "details come after")


@pytest.mark.parametrize("source", ["ad_clicktomsg", "story"])
def test_an_entry_hint_gives_no_behavioural_order(source: str) -> None:
    hint = (source_hint(source) or "").lower()
    assert hint
    for order in _ORDERS:
        assert order not in hint, f"{source} hint still commands: {order}"


def test_the_ad_hint_says_the_prefill_is_still_worth_answering() -> None:
    """It is not the lead's own words, but it is what they want to know."""
    hint = source_hint("ad_clicktomsg") or ""
    assert "answer it" in hint.lower()


def test_the_ad_hint_still_blocks_inventing_facts_about_the_lead() -> None:
    """A button tap reveals a topic, never a goal, an age or a budget."""
    hint = (source_hint("ad_clicktomsg") or "").lower()
    assert "no goal" in hint and "no budget" in hint


def test_no_entry_hint_contradicts_the_answer_first_rule() -> None:
    assert "FIRST sentence answers it" in contract("id")
    for source in ("ad_clicktomsg", "story"):
        assert "discovery question" not in (source_hint(source) or "").lower()


def test_an_unknown_entry_point_adds_nothing() -> None:
    """Organic leads get no assumptions at all."""
    assert source_hint(None) is None
    assert source_hint("organic") is None

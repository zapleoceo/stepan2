"""The one code-written reply must not name a business the branch has nothing to do with.

Until 2026-08-02 the greeting sent to a contentless first message was a literal naming this
product's first client. Every branch sent it — including the demo branch Meta reviews, where
"hi" is the likeliest opening word a reviewer types and, being a bare ack, lands exactly here
instead of with the model.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import re  # noqa: E402

import pytest  # noqa: E402

from app.modules.conversation.opener import JUNK_OPENER, Entry, classify, junk_opener  # noqa: E402
from app.modules.settings.schema import defaults  # noqa: E402


class _M:
    def __init__(self, direction: str, text: str) -> None:
        self.direction, self.text = direction, text


def test_the_fallback_names_no_business() -> None:
    assert not re.search(r"it\s*step|academy|minstep", JUNK_OPENER, re.IGNORECASE)


def test_a_branch_greeting_wins_over_the_fallback() -> None:
    assert junk_opener("Hi! I'm Stepan.") == "Hi! I'm Stepan."


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_unset_falls_back(empty: str | None) -> None:
    assert junk_opener(empty) == JUNK_OPENER


def test_whitespace_is_trimmed() -> None:
    assert junk_opener("  Halo Kak  ") == "Halo Kak"


def test_the_setting_ships_empty_so_no_branch_inherits_a_brand() -> None:
    assert defaults()["junk_opener"] == ""


@pytest.mark.parametrize("greeting", ["hi", "halo", "ok", "hai", "😊"])
def test_a_bare_greeting_still_routes_here(greeting: str) -> None:
    """Why this line matters at all: these are the openings that never reach the model."""
    assert classify([_M("in", greeting)], None, None).entry is Entry.JUNK


@pytest.mark.parametrize("typed", ["hello", "berapa harganya?", "I want the price"])
def test_real_words_go_to_the_model_instead(typed: str) -> None:
    assert classify([_M("in", typed)], None, None).entry is not Entry.JUNK

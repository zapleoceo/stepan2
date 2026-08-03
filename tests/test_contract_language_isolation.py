"""The prompt must not push one branch's language onto another's leads.

Until 2026-08-03 the shared contract carried Indonesian style rules — address the lead as
"Kak", call yourself "aku", never say "kampus", the KBBI note, our Jakarta address — and every
branch got them. The English-speaking demo branch therefore answered a Russian lead in
Indonesian, twice, and no single "reply in their language" line could outvote a page of
Indonesian instruction. Language now leads the hard rules, and the Indonesian style block is
served only to Indonesian branches.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.modules.conversation.free_mode import free_contract  # noqa: E402

# Words that only belong in an Indonesian conversation. "Anda"/"Kak" are forms of address,
# "kampus"/"KBBI"/"Menara" are the Jakarta-specific wording rule.
_ID_ONLY = ("Kak", "aku", "kampus", "KBBI", "Menara", "Anda")


@pytest.mark.parametrize("lang", ["en", "ru", "uk", "vi", "ms"])
@pytest.mark.parametrize("word", _ID_ONLY)
def test_no_indonesian_style_reaches_other_languages(lang: str, word: str) -> None:
    assert word not in free_contract(lang)


@pytest.mark.parametrize("word", _ID_ONLY)
def test_indonesian_branches_keep_every_rule(word: str) -> None:
    """The live Indonesian branch must lose nothing — this refactor is about the others."""
    assert word in free_contract("id")


@pytest.mark.parametrize("lang", ["id", "en", "ru", "ms"])
def test_language_rule_is_the_first_hard_rule(lang: str) -> None:
    """Position is the point: it used to sit eighth in the list, under a wall of Indonesian.

    Recency and primacy both matter to a model, and the language decision must not be the
    thing it reads last and weakest."""
    contract = free_contract(lang)
    rules_at = contract.index("HARD RULES")
    language_at = contract.index("LANGUAGE", rules_at)
    first_bullet = contract.index("\n-", rules_at)
    assert language_at - first_bullet < 5  # the language rule IS the first bullet


@pytest.mark.parametrize("lang", ["id", "en", "ru", "ms"])
def test_the_rule_says_the_lead_decides_not_the_prompt(lang: str) -> None:
    contract = free_contract(lang)
    assert "SAME language the lead used" in contract
    # The specific failure: the model copying the language of the examples around it.
    assert "never which language to speak" in contract


@pytest.mark.parametrize("lang", ["id", "en", "ru"])
def test_the_branch_language_is_only_the_fallback(lang: str) -> None:
    """It must be what to use when the lead's language is unclear — not the default answer."""
    assert "genuinely unclear" in free_contract(lang)


def test_malay_does_not_inherit_indonesian_forms() -> None:
    """Close languages, different registers: "Kak"/"aku" do not carry over to a Malay branch."""
    assert "Kak/Kakak" not in free_contract("ms")
    assert "Bahasa Melayu" in free_contract("ms")


@pytest.mark.parametrize("lang", ["id", "en", "ru", "ms"])
def test_the_contract_still_names_the_branch_language(lang: str) -> None:
    """The fallback has to be a language a person would name, not a code."""
    from app.modules.conversation.free_mode import language_name  # noqa: PLC0415

    assert language_name(lang) in free_contract(lang)

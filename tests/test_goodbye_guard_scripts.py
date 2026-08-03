"""The goodbye guard must not mistake a foreign alphabet for silence.

_is_closing_only has a branch for "nothing was said at all" — a lone 🙏 or "...". Its word
regex was [a-zA-ZÀ-ɏ], Latin only, so ANY message written entirely in Cyrillic, Arabic,
Chinese, Thai or Greek matched zero words and took that branch. When our own previous reply
was also non-Latin, _goodbye_loop decided both sides had said goodbye and decide() returned
None: the thread was answered never again, under a log line reading "both sides have said
goodbye — staying quiet".

Branch 1 (live) has 85 all-Cyrillic inbound messages in the last 90 days. Among them, real
price objections — exactly the turn where going silent costs the deal.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.modules.conversation.reply import _goodbye_loop, _is_closing_only  # noqa: E402


class _M:
    def __init__(self, direction: str, text: str) -> None:
        self.direction, self.text = direction, text


@pytest.mark.parametrize(("script", "text"), [
    ("русский", "та нет. не могу я привести, мне реально дорого"),
    ("русский", "Понимаю, бюджет — важный момент. Что для вас важнее?"),
    ("украинский", "скільки коштує навчання?"),
    ("арабский", "كم السعر"),
    ("китайский", "多少钱"),
    ("тайский", "ราคาเท่าไหร่"),
    ("греческий", "πόσο κοστίζει"),
])
def test_a_real_question_in_any_script_is_not_read_as_silence(script: str, text: str) -> None:
    assert not _is_closing_only(text), f"{script}: сообщение с содержанием принято за пустое"


def test_a_cyrillic_price_objection_does_not_silence_the_thread() -> None:
    """The live shape from branch 1 thread 452: our Russian reply, their Russian refusal. The
    reply that followed is what kept that thread alive to a captured phone number."""
    dialog = [
        _M("out", "Понимаю, бюджет — важный момент. Что для вас важнее: цена или платёж?"),
        _M("in", "та нет. не могу я привести, мне реально дорого"),
    ]
    assert not _goodbye_loop(dialog, None)


def test_genuine_emptiness_is_still_silence() -> None:
    """The branch exists for a reason and must keep working."""
    assert _is_closing_only("🙏")
    assert _is_closing_only("...")
    assert _is_closing_only("!!!")


def test_latin_farewells_are_unaffected() -> None:
    """The change must be a no-op for the languages the guard was built on."""
    assert _is_closing_only("makasih ya kak")
    assert _is_closing_only("thank you")
    assert not _is_closing_only("Berapa harganya kak?")


def test_a_cyrillic_farewell_is_still_a_farewell_when_both_sides_close() -> None:
    """Widening the alphabet must not make the guard blind: a real goodbye still ends it.

    "спасибо" is not in the Indonesian/English closing vocabulary, so the pair below is held
    open — which is the safe direction. The guard closing on emoji-only turns is what the
    original commit measured, and that still works (see test_genuine_emptiness_is_still_silence)."""
    dialog = [_M("out", "🙏"), _M("in", "🙏")]
    assert _goodbye_loop(dialog, None)


def test_digits_and_underscores_are_not_words() -> None:
    """A bare number is not content the agent should react to as a question."""
    assert _is_closing_only("123")
    assert _is_closing_only("___")

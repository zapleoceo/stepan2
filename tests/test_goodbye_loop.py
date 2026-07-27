"""When both sides have said goodbye, the right reply is none at all.

The sales contract asks the model to let a finished conversation finish, and the model does
not: every turn looks new to it, so it answers "one last short time" again and again. Two
persona runs ended this way — thirteen turns of "sampai jumpa" in one, seventeen in another,
the bot eventually reduced to sending a lone 🙏 over and over. Each of those is a real message
against the account's send budget, to someone who stopped talking long ago.

The rule requires BOTH sides to be closing. A bare "oke" answering a real message of ours is a
lead still listening, and that turn must still get a reply — which is why the check looks at
our previous message too.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.conversation.reply import _goodbye_loop


def _msg(direction: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(direction=direction, text=text)


def test_the_live_loop_is_stopped() -> None:
    dialog = [
        _msg("in", "Oke sip, udah clear. Makasih Min, saya tunggu besok pagi ya."),
        _msg("out", "Sama-sama, Kak. Sampai jumpa!"),
        _msg("in", "Siap, Kak. Makasih, aku tunggu."),
    ]
    assert _goodbye_loop(dialog)


def test_a_lone_emoji_reply_of_ours_still_counts_as_a_farewell() -> None:
    assert _goodbye_loop([_msg("in", "makasih kak"), _msg("out", "🙏"), _msg("in", "siap")])


@pytest.mark.parametrize("last_in", [
    "oke, daftarin aku ya",
    "makasih, tapi berapa harganya?",
    "siap kak, nomor WA aku 081234567890",
    "ya, saya mau ikut yang 1 hari",
])
def test_a_closing_word_carrying_content_is_still_answered(last_in: str) -> None:
    """The narrow part of the rule. "Oke" is how half of Indonesia starts a sentence."""
    dialog = [_msg("in", "halo"), _msg("out", "Sampai jumpa!"), _msg("in", last_in)]
    assert not _goodbye_loop(dialog)


def test_a_bare_ok_after_a_real_answer_is_still_answered() -> None:
    """They may be reading and about to ask something — we have not closed, so neither have
    they. Only a mutual goodbye ends the turn."""
    dialog = [
        _msg("in", "berapa biayanya?"),
        _msg("out", "Vibe Coding Rp 13.000.000, bisa dicicil 4x. Kakak mau info jadwalnya?"),
        _msg("in", "oke"),
    ]
    assert not _goodbye_loop(dialog)


def test_an_opening_message_is_never_a_goodbye() -> None:
    assert not _goodbye_loop([_msg("in", "halo")])
    assert not _goodbye_loop([])


def test_our_farewell_carrying_the_lead_s_name_still_counts() -> None:
    """"Sampai besok, Kak Rani" is a goodbye, but "Rani" is not a pleasantry — without the
    name the guard saw content in our own message and answered again, which is how a sim
    conversation still reached seventeen turns with the guard in place."""
    dialog = [
        _msg("in", "makasih kak"),
        _msg("out", "Sampai besok, Kak Rani. 😊"),
        _msg("in", "Siap kak, sampai besok! 😊"),
    ]
    assert not _goodbye_loop(dialog)                      # без имени — не срабатывает
    assert _goodbye_loop(dialog, "Rani Putri")            # с именем — срабатывает


def test_a_name_does_not_make_a_real_question_look_like_a_goodbye() -> None:
    dialog = [
        _msg("in", "halo"),
        _msg("out", "Sampai jumpa, Kak Rani!"),
        _msg("in", "Kak Rani mau tanya harganya berapa"),
    ]
    assert not _goodbye_loop(dialog, "Rani")


# ── the vocabulary-free half: nobody is saying anything new ───────────────────

def test_the_live_loop_the_word_list_kept_missing() -> None:
    """Round 4, seventeen turns. Every one of these is unmistakably a goodbye, and every one
    carries a word no list had — `ah`, `kalo`, `perlu`, `selamat`, `malam`. Widening the list
    a third time is a patch; this is the shape instead."""
    dialog = [
        _msg("in", "Udah ah Kak, makasih. Nanti gue WA aja kalo perlu. Bye! 😊"),
        _msg("out", "Sama-sama Kak Bagus. Selamat malam, sampai nanti ya 😊"),
        _msg("in", "Iya Kak, makasih. Nanti gue tunggu WA nya ya! Bye."),
        _msg("out", "Siap Kak Bagus, selamat malam dan sampai jumpa nanti ya 😊"),
    ]
    assert _goodbye_loop(dialog)


def test_a_new_question_breaks_the_loop_immediately() -> None:
    """The property a word list cannot have: anything the lead introduces ends it."""
    dialog = [
        _msg("in", "Iya Kak, makasih. Nanti gue tunggu WA nya ya! Bye."),
        _msg("out", "Siap Kak Bagus, selamat malam dan sampai jumpa nanti ya 😊"),
        _msg("in", "Eh tunggu, kelas yang 1 hari itu biayanya berapa ya?"),
    ]
    assert not _goodbye_loop(dialog)


def test_two_different_answers_of_ours_are_not_a_loop() -> None:
    """Both sides must be repeating. A lead saying "oke" twice to two real answers is a lead
    still being sold to."""
    dialog = [
        _msg("in", "oke"),
        _msg("out", "Vibe Coding 4 bulan, 37 sesi, mulai 1 September 2026."),
        _msg("in", "oke"),
        _msg("out", "Biayanya Rp 13.000.000, bisa dicicil 4x Rp 3.250.000 per bulan."),
    ]
    assert not _goodbye_loop(dialog)

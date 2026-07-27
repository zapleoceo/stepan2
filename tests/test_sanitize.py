"""Outgoing text is sanitized before delivery — see conversation/sanitize.py."""
from __future__ import annotations

from app.modules.conversation.sanitize import clean_reply

# ── URLs must stay tappable ───────────────────────────────────────────────────

def test_a_full_stop_after_a_url_is_dropped() -> None:
    """39 live messages in 30 days ended a sentence right after the link. Instagram's
    autolinker swallows the dot into the address, so the lead taps a URL that 404s."""
    out = clean_reply("Detailnya di https://itstep.id/python.")
    assert out.endswith("/python")
    assert "python." not in out


def test_a_dot_inside_a_url_is_untouched() -> None:
    assert "itstep.id" in clean_reply("Cek https://itstep.id/data-analyst ya Kak")
    assert clean_reply("https://drive.google.com/file/d/1Abc/view") \
        == "https://drive.google.com/file/d/1Abc/view"


def test_a_url_mid_sentence_keeps_the_following_text() -> None:
    out = clean_reply("Lihat https://itstep.id/python. Harganya 13 juta.")
    assert "https://itstep.id/python Harganya" in out or "/python\n" in out or "/python " in out


# ── a double-escaped newline must not reach the lead ──────────────────────────

def test_a_literal_backslash_n_becomes_a_real_line_break() -> None:
    """The model double-escapes its own reply, json.loads unescapes one level, and the lead
    sees two visible characters. Live on 27.07 in five messages, all of them long
    money-answers: "…tegas banget!\n\nTapi jujur ya Kak"."""
    out = clean_reply("tegas banget!" + chr(92) + "n" + chr(92) + "nTapi jujur ya Kak")
    assert chr(92) + "n" not in out
    assert out == "tegas banget!\n\nTapi jujur ya Kak"


def test_a_literal_crlf_pair_is_also_unescaped() -> None:
    out = clean_reply("baris satu" + chr(92) + "r" + chr(92) + "nbaris dua")
    assert out == "baris satu\nbaris dua"


def test_a_line_revealed_by_unescaping_is_still_phone_filtered() -> None:
    """Unescaping runs BEFORE the per-line phone filter, so a fabricated number hidden behind
    a literal \n is caught like any other line."""
    out = clean_reply("Halo Kak" + chr(92) + "nWA saya 0812 3456 7890 ya")
    assert "0812" not in out
    assert "Halo Kak" in out


def test_real_backslashes_that_are_not_escapes_survive() -> None:
    assert "C:" + chr(92) + "Users" in clean_reply("path C:" + chr(92) + "Users")

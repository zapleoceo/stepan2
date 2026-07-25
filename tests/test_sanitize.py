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

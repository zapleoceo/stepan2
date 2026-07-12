"""_most_similar_prior: word-overlap catches a REWORDED repeat (threads 2047/2143) that the
char-sequence ratio slides under, without false-positiving on genuinely different messages."""
from __future__ import annotations

from types import SimpleNamespace

from app.modules.conversation.reply import _DUPLICATE_RATIO, _most_similar_prior


def _out(text: str) -> SimpleNamespace:
    return SimpleNamespace(direction="out", text=text)


def test_reworded_opener_is_caught_by_word_overlap() -> None:
    # thread 2143: a follow-up re-sent the opener with only the topic word swapped
    prior = ("Hai Kak, terima kasih sudah tertarik! Boleh tahu apa tujuan utama Kakak "
             "belajar coding? Misalnya ingin kerja di bidang IT")
    nudge = ("Hai Kak, terima kasih sudah tertarik! Boleh tahu apa tujuan utama Kakak "
             "belajar back-end development? Misalnya ingin kerja remote")
    _, ratio = _most_similar_prior(nudge, [_out(prior)])
    assert ratio >= _DUPLICATE_RATIO  # flagged as a duplicate → regen / drop


def test_distinct_messages_do_not_collide() -> None:
    prior = "Program SMM Intensive 2 minggu, investasinya Rp 1.882.955, DP 500rb amankan seat."
    other = ("Kalau boleh tahu, apa tantangan terbesar Kakak saat ini dalam bikin konten "
             "yang menarik untuk brand?")
    _, ratio = _most_similar_prior(other, [_out(prior)])
    assert ratio < _DUPLICATE_RATIO  # different topics → not a duplicate


def test_short_lines_skip_word_overlap() -> None:
    # both under the 5-content-word floor → Jaccard is NOT applied; a couple shared words on
    # short, otherwise-different lines must not be forced to a duplicate by word overlap
    _, ratio = _most_similar_prior("Boleh minta nomor WA-nya Kak?", [_out("Program mana nih Kak?")])
    assert ratio < _DUPLICATE_RATIO

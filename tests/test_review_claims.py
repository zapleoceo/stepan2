"""What our reviews SAY is not something anyone here has read.

Pointing a doubtful lead at the address on Google Maps is right and verifiable — the knowledge
base says so, because trust in this market is won with things the person can open themselves.
Asserting what the reviews contain is the opposite: the lead opens the map while still in the
chat, and an empty or unrelated page lands at the exact moment they were deciding whether we
are real.

Banned in facts_policy on 27.07 after the bot invented it once; produced again on the very
next sim run — "di sana banyak review dari siswa dari berbagai program" — which is what moved
it out of prose and into the gate.
"""
from __future__ import annotations

from app.modules.conversation.guard import review_content_claims
from app.modules.conversation.money_gate import money_issues

_KB = "Alamat kami Menara Sudirman lantai 8. Kursus Vibe Coding 13.000.000."


def test_the_live_case_is_blocked() -> None:
    reply = ("Kakak bisa cek Google Maps kami - di sana banyak review dari siswa dari "
             "berbagai program")
    assert review_content_claims(reply)
    assert any("reviews say" in i for i in money_issues(reply, _KB))


def test_pointing_at_the_address_is_still_allowed() -> None:
    """The verifiable half must survive — it is the KB's own answer to "penipu"."""
    reply = ("Kalau ragu, alamat kami ada di Google Maps: Menara Sudirman lantai 8. "
             "Mampir aja Senin-Jumat jam 10-17")
    assert review_content_claims(reply) == []
    assert money_issues(reply, _KB) == []


def test_a_bare_invitation_to_read_reviews_is_allowed() -> None:
    """Inviting them to look makes no claim about what they will find."""
    assert review_content_claims("Boleh cek review kami di Google Maps ya Kak") == []


def test_ratings_we_never_measured_are_blocked() -> None:
    assert review_content_claims("rating kami bagus banget di Google Maps")


def test_counting_reviews_is_blocked() -> None:
    assert review_content_claims("ada ratusan ulasan dari alumni di sana")

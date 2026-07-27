"""A named person may not be claimed as ours unless the knowledge base names them.

Thread 2367, 9 July: "Contoh alumni kami, Pieter Levels, membangun aplikasi web full-stack
dengan AI…". Pieter Levels is a well-known indie hacker with no connection to the school —
zero mentions across all 18 knowledge documents — and the claim is checkable in one search.

facts_market already forbids this twice in prose ("НИКОГДА не говорить 'один из наших
выпускников…' без реального связанного кейса"; a public figure must be labelled as external).
It went out anyway. A rule stated only in prose is a rule the gate cannot keep.
"""
from __future__ import annotations

from app.modules.conversation.guard import fabricated_alumni_claim
from app.modules.conversation.money_gate import money_issues

_KB = """Kursus Vibe Coding 13.000.000. Bukti nyata: MinStep, asisten AI yang menulis pesan
ini, dibuat oleh alumni Vibe Coding. Kisah jaringan internasional: Eduard Khudaiberdin (Amazon),
Taylor Kroot (Facebook)."""


def test_the_live_case_is_blocked() -> None:
    reply = "Contoh alumni kami, Pieter Levels, membangun aplikasi web full-stack dengan AI"
    assert fabricated_alumni_claim(reply, _KB) == ["Pieter Levels"]
    assert any("Pieter Levels" in issue for issue in money_issues(reply, _KB))


def test_the_name_first_word_order_is_also_caught() -> None:
    assert fabricated_alumni_claim("Budi Santoso itu alumni kami lho Kak", _KB) \
        == ["Budi Santoso"]


def test_a_name_the_knowledge_base_actually_contains_is_fine() -> None:
    """The international-network stories are real and named in facts_market — quoting them is
    what the KB asks for, not what it forbids."""
    reply = "Eduard Khudaiberdin, alumni kami di jaringan internasional, kerja di Amazon"
    assert fabricated_alumni_claim(reply, _KB) == []


def test_an_example_labelled_external_passes() -> None:
    """facts_market's own instruction: a public figure may be used if marked as an outside
    example. The gate must not punish the correct behaviour."""
    reply = "Pieter Levels itu contoh dari luar, bukan alumni kami, tapi caranya mirip"
    assert fabricated_alumni_claim(reply, _KB) == []


def test_a_claim_about_the_group_with_no_name_is_allowed() -> None:
    """"Many of our alumni started freelancing" is the honest archetype the KB permits; only a
    specific person's name turns it into a verifiable assertion about a human being."""
    assert fabricated_alumni_claim(
        "Banyak alumni kami yang mulai freelance setelah lulus", _KB) == []


def test_a_mentor_or_staff_name_is_not_an_alumni_claim() -> None:
    assert fabricated_alumni_claim("Mentornya Bayu Prasetyo ya Kak", _KB) == []


def test_bubbles_are_checked_separately() -> None:
    reply = "Halo Kak|||Alumni kami Rizky Pratama sekarang freelance"
    assert fabricated_alumni_claim(reply, _KB) == ["Rizky Pratama"]

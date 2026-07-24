"""Deterministic per-turn coaching notes and the bare-ack either-or (Phase 2). These fire on
code-certain signals so the model can't ignore them the way it ignored the prose-only versions
(thread 5042: "iya kk" ×12; 5049 salary → hold-line; 5064 buying signal → re-discovery)."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.conversation.dossier import LeadDossier
from app.modules.conversation.reply import (
    BARE_ACK_NOTE,
    BUYING_SIGNAL_NOTE,
    CLOSING_NOTE,
    DISCOVERY_CAP_NOTE,
    DISENGAGEMENT_NOTE,
    SALARY_NOTE,
    _consecutive_bare_acks,
    _turn_note,
)


@dataclass
class _M:
    direction: str
    text: str


def _d(*turns: tuple[str, str]) -> list[_M]:
    return [_M(dir_, txt) for dir_, txt in turns]


# ── salary / outcome (thread 5049) ───────────────────────────────────────────

def test_salary_question_gets_the_answer_note() -> None:
    assert _turn_note(_d(("in", "gaji SMM specialist berapa kak?"))) is SALARY_NOTE
    assert _turn_note(_d(("in", "prospek kerjanya gimana ya"))) is SALARY_NOTE


def test_salary_note_forbids_hold_and_phone_ask() -> None:
    assert "NEVER" in SALARY_NOTE and "hold-line" in SALARY_NOTE


# ── disengagement (threads 5091, 5096) ───────────────────────────────────────

def test_trolling_gets_the_disengagement_note() -> None:
    assert _turn_note(_d(("in", "Ari kmu mabok??"))) is DISENGAGEMENT_NOTE
    assert _turn_note(_d(("in", "repost akun gue dong"))) is DISENGAGEMENT_NOTE


def test_disengagement_beats_everything_else() -> None:
    """A troll who also happens to say a yes-word still gets the don't-sell note."""
    assert _turn_note(_d(("out", "mau daftar?"), ("in", "boleh, followback dulu"))) \
        is DISENGAGEMENT_NOTE


# ── buying signal (threads 5039, 5064) ───────────────────────────────────────

def test_yes_after_an_offer_advances_not_rediscovers() -> None:
    d = _d(("out", "Mau lihat contoh proyeknya di kampus?"), ("in", "Boleh"))
    assert _turn_note(d) is BUYING_SIGNAL_NOTE


def test_bare_yes_to_an_open_question_is_not_a_buying_signal() -> None:
    """'iya' to 'proyek apa?' is filler, not acceptance (thread 5042) — must not route to the
    advance note; with two in a row it's the bare-ack note instead."""
    d = _d(("out", "proyek kuliah apa yang mau dibikin?"), ("in", "iya kak"),
           ("out", "yang gimana kak?"), ("in", "iya kk"))
    assert _turn_note(d) is BARE_ACK_NOTE


# ── bare-ack counter + either-or (thread 5042) ───────────────────────────────

def test_bare_ack_counter_covers_the_variants_that_slipped() -> None:
    """'iya kk' / 'iya kaka' / 'iya boleh' all used to slip the 'kak'-only pattern."""
    d = _d(("in", "iya kk"), ("out", "?"), ("in", "iya kaka"), ("out", "?"),
           ("in", "iya boleh"))
    assert _consecutive_bare_acks(d) == 3


def test_a_real_message_resets_the_bare_ack_counter() -> None:
    d = _d(("in", "iya kk"), ("in", "aku mau belajar bikin app"), ("in", "iya kak"))
    assert _consecutive_bare_acks(d) == 1


# ── discovery cap (thread 5039) ──────────────────────────────────────────────

def test_discovery_cap_note_after_enough_questions_with_empty_dossier() -> None:
    d = _d(("in", "a"), ("in", "b"), ("in", "c"), ("in", "d"), ("in", "e"))
    assert _turn_note(d, LeadDossier()) is DISCOVERY_CAP_NOTE


def test_discovery_landed_triggers_the_closing_note() -> None:
    """Once a pain AND a goal are known, stop discovering and CLOSE (Phase 3.1). Only 5% of
    leads gave a phone — the bot kept the conversation open on warm leads."""
    d = _d(("in", "iya bener banget, aku emang pengen mulai"))
    dossier = LeadDossier(pains=["takut telat"], desired_state=["kerja remote"])
    assert _turn_note(d, dossier) is CLOSING_NOTE


def test_no_closing_note_once_the_lead_is_ready() -> None:
    """A lead already flagged ready is in the hand-off path — don't re-close them."""
    d = _d(("in", "oke lanjut"))
    dossier = LeadDossier(pains=["x"], desired_state=["y"], readiness="ready")
    assert _turn_note(d, dossier) is not CLOSING_NOTE


def test_closing_note_asks_for_contact_with_honest_urgency() -> None:
    assert "WhatsApp" in CLOSING_NOTE and "Never invent" in CLOSING_NOTE


def test_a_fresh_fear_defers_the_closing_note() -> None:
    """Sim p3-close: dossier had landed so closing fired, but the lead just said 'takutnya
    aku ga bisa coding' — closing over a live fear steamrolls and trips the pitch gate. The
    worry gets handled first."""
    d = _d(("in", "takutnya aku ga bisa coding sih"))
    dossier = LeadDossier(pains=["manual ribet"], desired_state=["order online"])
    assert _turn_note(d, dossier) is not CLOSING_NOTE

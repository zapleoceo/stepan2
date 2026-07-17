"""Gates from the 2026-07-17 sales audit of 300 live threads.

- premature_payment_details: thread 4114 — a schoolkid who only said 'saya mau kerja'
  got the BCA account and a DP instruction for a course they never chose.
- invented_price_no_card: thread 4188 quoted Rp 26.000.000 for a 13.36M course; a sim
  turn whose context missed the card invented Rp 7.000.000 for SMM Intensive.
- PAYMENT_INTENT nudge: thread 2821 — 'No rek min' (ready to pay!) got a pitch and two
  WhatsApp asks instead of the payment details.
- clarify menu: the open-ended 'sebutkan lebih spesifik' fired on ~24 engaged threads.
"""
from __future__ import annotations

from app.modules.conversation import guard
from app.modules.conversation.needs import NeedsProfile
from app.modules.conversation.situations import (
    PAYMENT_INTENT_NUDGE,
    pick_nudge,
)


class _Msg:
    def __init__(self, direction: str, text: str) -> None:
        self.direction = direction
        self.text = text


BANK_REPLY = ("Silakan transfer DP Rp 500.000 ke rekening BCA 5245550101 "
              "a.n. PT. ITSTEP ACADEMY IND ya Kak 🙏")


def test_bank_details_blocked_when_lead_never_asked_to_pay() -> None:
    issues = guard.premature_payment_details(BANK_REPLY, "saya mau kerja")
    assert issues and "bank account" in issues[0]


def test_bank_details_allowed_when_lead_asked_how_to_pay() -> None:
    assert guard.premature_payment_details(BANK_REPLY, "oke, cara bayarnya gimana?") == []
    assert guard.premature_payment_details(BANK_REPLY, "no rek min") == []
    assert guard.premature_payment_details(BANK_REPLY, "mau daftar kak") == []


def test_payment_methods_without_account_number_are_fine() -> None:
    reply = "Bisa transfer, QRIS, atau kartu di kampus ya Kak 😊"
    assert guard.premature_payment_details(reply, "halo") == []


def test_price_with_no_price_in_context_is_flagged() -> None:
    issues = guard.invented_price_no_card(
        "Investasinya Rp 7.000.000, cicilan 4 × Rp 1.750.000.", "SMM Intensive: kelas 2 minggu")
    assert issues and "never state a number" in issues[0]


def test_price_matching_context_is_not_flagged_here() -> None:
    ctx = "SMM Intensive — biaya Rp 1.882.955, DP Rp 500.000"
    assert guard.invented_price_no_card("Biayanya Rp 1.882.955 ya Kak", ctx) == []
    # a wrong figure against a priced context is the verify layer's job, not this gate's
    assert guard.invented_price_no_card("Biayanya Rp 7.000.000", ctx) == []


def test_no_price_in_reply_is_clean() -> None:
    assert guard.invented_price_no_card("Kelasnya seru banget Kak 😊", "") == []


def test_clarify_fallback_is_a_menu_not_a_brushoff() -> None:
    assert "1️⃣" in guard.CLARIFY_FALLBACK
    assert "sebutkan lebih spesifik" not in guard.CLARIFY_FALLBACK


def _dialog_with(last: str) -> list[_Msg]:
    return [_Msg("in", "halo kak"), _Msg("out", "halo!"), _Msg("in", last)]


def test_no_rek_picks_the_payment_intent_nudge() -> None:
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with("No rek min"), last_txt="No rek min",
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and nudge.startswith(PAYMENT_INTENT_NUDGE.split("\n")[0][:30])


def test_cara_bayar_picks_the_payment_intent_nudge() -> None:
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with("gimana cara bayarnya kak?"),
        last_txt="gimana cara bayarnya kak?", stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and "HOW or WHERE to pay" in nudge


def test_plain_price_question_does_not_trigger_payment_intent() -> None:
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with("berapa biayanya?"),
        last_txt="berapa biayanya?", stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is None or "HOW or WHERE to pay" not in nudge


def test_ingin_bergabung_gets_the_small_step_nudge() -> None:
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with("saya ingin bergabung.."),
        last_txt="saya ingin bergabung..", stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and "WANT TO JOIN" in nudge


def _dialog_with_menu(last: str) -> list[_Msg]:
    return [_Msg("in", "halo"),
            _Msg("out", "Pilih ya: 1️⃣ Karier 2️⃣ Bisnis 3️⃣ Skill 4️⃣ Anak"),
            _Msg("in", last)]


def test_menu_digit_converts_instead_of_more_discovery() -> None:
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with_menu("1"), last_txt="1",
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and "answered your numbered menu" in nudge


def test_bare_digit_without_a_menu_is_not_a_menu_reply() -> None:
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with("1"), last_txt="1",
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is None or "answered your numbered menu" not in nudge


def test_own_post_share_is_interest_not_broken_media() -> None:
    dialog = [_Msg("in", "halo kak"), _Msg("out", "halo!"),
              _Msg("in", "📷 itstep_jakarta")]
    nudge = pick_nudge(
        lead_type=None, dialog=dialog, last_txt="📷 itstep_jakarta",
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and "OUR OWN Instagram post" in nudge


def test_foreign_share_still_gets_the_unseen_media_nudge() -> None:
    dialog = [_Msg("in", "halo kak"), _Msg("out", "halo!"),
              _Msg("in", "📷 dramaindonesia")]
    nudge = pick_nudge(
        lead_type=None, dialog=dialog, last_txt="📷 dramaindonesia",
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and "OUR OWN" not in nudge


def test_two_questions_get_the_answer_every_part_suffix() -> None:
    txt = "yang dipelajari apa? ada jaminan kerja?"
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with(txt), last_txt=txt,
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and "EVERY part" in nudge


def test_budget_objection_in_plain_words_hits_the_cheap_entry_nudge() -> None:
    txt = "Kendala saya di budget, biayanya terasa berat"
    nudge = pick_nudge(
        lead_type=None, dialog=_dialog_with(txt), last_txt=txt,
        stored_needs=NeedsProfile(), inbound_count=2)
    assert nudge is not None and ("cheap" in nudge.lower() or "budget" in nudge.lower()
                                  or "murah" in nudge.lower() or "entry" in nudge.lower())

"""Situational-nudge detectors (soft-no / low-budget / minor) — deterministic triggers that
carry the Jakarta-methodology rules the model followed unreliably at prompt scale."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.modules.conversation.situations import (  # noqa: E402
    AD_TEMPLATE_RE as _AD_TEMPLATE_RE,
)
from app.modules.conversation.situations import (
    LOW_BUDGET_RE as _LOW_BUDGET_RE,
)
from app.modules.conversation.situations import (
    MINOR_RE as _MINOR_RE,
)
from app.modules.conversation.situations import (
    SOFT_NO_RE as _SOFT_NO_RE,
)
from app.modules.conversation.situations import (
    is_answerable_question as _is_answerable_question,
)
from app.modules.conversation.situations import (
    unseen_media_in_turn as _unseen_media_in_turn,
)


def _answer_first_fires(text: str) -> bool:
    """The exact gate used in decide(): a real question, but never the ad prefill."""
    return _is_answerable_question(text) and not _AD_TEMPLATE_RE.search(text)


def test_answer_first_fires_on_real_questions() -> None:
    # every one of these is a live 3-day-audit case that got the clarify stub instead
    for s in ["Berbayar berapa", "Biaya nya berapa?", "apakah dibayar",
              "Modal hp bisa ga sih min", "untuk jadwalnya hari apa aja ya biasanya?",
              "Untuk ikut serta caranya gimana ya kak?", "sertifikatnya BNSP kan kak?",
              "ini gratis atau ada biaya nya kak",
              # sim s10: bare 'cicil' without the -an suffix got the WA-stub dodge
              "bs cicil ga"]:
        assert _answer_first_fires(s), s


def test_answer_first_never_fires_on_ad_prefill() -> None:
    # the ad button click mentions 'biaya' but must NOT get a price — _AD_OPENER_NUDGE owns it
    for s in ["Halo, saya ingin tahu detail program SMM dan biaya kursusnya 😊",
              "💻 Ceritakan lebih detail tentang program kursusnya",
              "🐍 Ceritakan lebih detail tentang program kursus Python",
              # third prefill family, 163 threads by 2026-07-19 — unrecognized, it made the
              # bot dump a full price block as its FIRST message (thread 4500)
              "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?"]:
        assert not _answer_first_fires(s), s


def test_answer_first_ignores_non_questions() -> None:
    for s in ["oke makasih", "iya kak", "Mantap"]:
        assert not _answer_first_fires(s), s


def test_buying_signal_catches_gas_family() -> None:
    from app.modules.conversation.situations import BUYING_SIGNAL_RE
    # sim s10 slang_minimal: 'yaudh gas' (= go ahead) got the clarify menu at the buying moment
    for s in ["yaudh gas", "gaskeun kak", "oke gas", "mau daftar sekarang"]:
        assert BUYING_SIGNAL_RE.search(s), s
    assert not BUYING_SIGNAL_RE.search("saya masih mikir dulu")


# ─── unseen media: the lead sent something the bot cannot read ───

class _M:
    def __init__(self, direction: str, text: str) -> None:
        self.direction, self.text = direction, text


def test_unseen_media_detects_unreadable_content() -> None:
    for txt in ["🎬 Message unavailable · This content may have been deleted by its owner or "
                "hidden by their privacy settings.",
                "📷 dramaindonesia.official",   # bare share, no caption to read
                "🖼 media",                      # image the broker never described
                "🎤 voice"]:                     # voice never transcribed
        assert _unseen_media_in_turn([_M("out", "hai"), _M("in", txt)]), txt


def test_unseen_media_found_even_when_not_last_message() -> None:
    # thread 3058: unavailable reel, THEN 'Like2 ders' — last-message-only checks miss it
    dialog = [_M("out", "hai kak"),
              _M("in", "🎬 Message unavailable · This content may have been deleted by its owner"),
              _M("in", "Like2 ders")]
    assert _unseen_media_in_turn(dialog)


def test_unseen_media_ignores_readable_turns_and_older_history() -> None:
    # a share WITH a caption is readable — don't claim blindness
    assert not _unseen_media_in_turn(
        [_M("out", "hai"), _M("in", "📷 itstep_jakarta · Masih scroll tapi belum menghasilkan?")])
    # plain text turn
    assert not _unseen_media_in_turn([_M("out", "hai"), _M("in", "berapa harganya kak?")])
    # an unreadable item from an EARLIER turn (before our last send) is already handled
    assert not _unseen_media_in_turn(
        [_M("in", "🖼 media"), _M("out", "aku belum bisa lihat"), _M("in", "oke kak")])


def test_soft_no_detects_polite_refusals() -> None:
    for s in ["nanti aja deh kak", "saya pikir dulu ya", "insya allah lain kali",
              "belum ada biaya kak", "next time aja", "mau tanya istri dulu",
              "diskusi sama orang tua dulu ya", "ga dulu deh", "nabung dulu"]:
        assert _SOFT_NO_RE.search(s), s


def test_soft_no_ignores_engaged_replies() -> None:
    for s in ["iya kak mau daftar", "boleh minta linknya?", "oke lanjut",
              "jadwalnya kapan kak?", "saya tertarik banget"]:
        assert not _SOFT_NO_RE.search(s), s


def test_low_budget_detects_money_signals() -> None:
    for s in ["ga ada modal kak", "belum punya uang", "mahal banget",
              "kemahalan kak", "gratisan aja bisa?", "masih nganggur",
              "butuh kerja bukan sertifikat", "ga ada ongkos ke sana"]:
        assert _LOW_BUDGET_RE.search(s), s


def test_low_budget_ignores_neutral() -> None:
    for s in ["harganya berapa kak?", "bisa dicicil ga?", "ada diskon ga"]:
        assert not _LOW_BUDGET_RE.search(s), s


def test_minor_detects_school_signals() -> None:
    for s in ["saya masih SMA kak", "anak saya mau ikut", "masih sekolah kak",
              "kelas 12 nih", "umur 15 kak", "16 tahun"]:
        assert _MINOR_RE.search(s), s


def test_minor_does_not_collide_with_one_day_class() -> None:
    # 'kelas 1 hari' (Skill Booster) / '5 jam' must NOT read as a school grade
    for s in ["kelas 1 hari itu berapa?", "yang 5 jam aja", "kelas online bisa?",
              "kelas 1 hari cocok buat coba"]:
        assert not _MINOR_RE.search(s), s


# ─── pick_nudge: the ONE priority chain, incl. conflict combos ───

from app.modules.conversation.needs import NeedsProfile  # noqa: E402
from app.modules.conversation.situations import (  # noqa: E402
    AD_OPENER_NUDGE,
    ANSWER_FIRST_NUDGE,
    ANSWER_FIRST_TIGHT_BUDGET_NUDGE,
    DISCOVERY_TURN_CAP,
    NEED_PAYOFF_NUDGE,
    SOFT_NO_NUDGE,
    SOFT_NO_WITH_QUESTION_NUDGE,
    pick_nudge,
)

_SPOKE = [_M("in", "aku mau belajar coding kak")]  # the lead has real words on record


def _pick(last_txt: str, needs: NeedsProfile | None = None, n: int = 2) -> str | None:
    return pick_nudge(lead_type="warm", dialog=[*_SPOKE, _M("in", last_txt)],
                      last_txt=last_txt, stored_needs=needs or NeedsProfile(),
                      inbound_count=n)


def test_combo_soft_no_with_question_answers_then_eases_off() -> None:
    # 'nanti dulu… tapi berapa harganya?' — neither half may be dropped: answer, then ease.
    got = _pick("nanti dulu deh kak, tapi berapa sih harganya?")
    assert SOFT_NO_WITH_QUESTION_NUDGE in got
    # a plain stall without a question keeps the pure soft-no handling
    assert SOFT_NO_NUDGE in _pick("nanti dulu deh kak")


def test_combo_question_from_tight_budget_answers_with_cheap_entry() -> None:
    # 'ga ada modal, berapa biayanya?' — honest number + the affordable entry beside it
    got = _pick("ga ada modal kak, berapa biayanya?")
    assert ANSWER_FIRST_TIGHT_BUDGET_NUDGE in got
    # a price question with no budget signal is NOT the tight-budget combo (it gets the
    # framed price answer instead — see the price-no-pain tests below)
    assert ANSWER_FIRST_TIGHT_BUDGET_NUDGE not in _pick("berapa biayanya kak?")


def test_format_mirror_rides_on_top_of_the_situation() -> None:
    # formatting is orthogonal to the situation: the answer-first instruction must survive,
    # with the length anchor appended — not replaced by it (that would re-open the worst leak)
    got = _pick("jadwalnya hari apa kak?")
    assert ANSWER_FIRST_NUDGE in got and "characters" in got


def test_format_mirror_suffix_fires_for_a_short_non_opener_message() -> None:
    # the mirror content itself: a short lead message (not the ad opener) gets the length
    # anchor. (A spoken lead now always has SOME situation, so the suffix rides on top — the
    # rides-on-top case is covered separately; here we check the suffix function's own output.)
    from app.modules.conversation.situations import format_suffix
    assert "one-liners" in format_suffix("oke kak", None)
    assert format_suffix("oke kak", None)  # short → suffix present
    # a long message gets no mirror (they wrote paragraphs, answer in kind)
    essay = "a" * 300
    assert format_suffix(essay, None) == ""


def test_format_mirror_skips_the_numbered_opener() -> None:
    # AD_OPENER's 3-bubble numbered opener is deliberate — the mirror must not fight it
    ad = "🐍 Ceritakan lebih detail tentang program kursus Python"
    got = pick_nudge(lead_type=None, dialog=[_M("in", ad)], last_txt=ad,
                     stored_needs=NeedsProfile(), inbound_count=1)
    assert got == AD_OPENER_NUDGE  # untouched, no suffix


def test_format_mirror_lets_a_long_message_get_a_fuller_answer() -> None:
    essay = ("saya sudah lama tertarik dengan coding karena menurut saya kemampuan berpikir "
             "kritis itu penting sekali untuk karier saya ke depan dan juga bisnis keluarga")
    got = _pick(essay)
    assert got is None or "one-liners" not in got


def test_need_payoff_respects_discovery_cap() -> None:
    # pain with no gain asks for the payoff — but only until the cap releases the lead;
    # past it the discovery-cap nudge presents on what we have (no endless interrogation)
    needs = NeedsProfile(pains=["takut gagal"], gains=[])
    assert NEED_PAYOFF_NUDGE in _pick("oke kak", needs=needs, n=2)
    past_cap = _pick("oke kak", needs=needs, n=DISCOVERY_TURN_CAP + 1)
    assert past_cap is not None and NEED_PAYOFF_NUDGE not in past_cap  # falls to discovery-cap


# ─── the lead's own auto-responder is not the lead ───

from app.modules.conversation.situations import (  # noqa: E402
    is_auto_reply,
    lead_spoke_own_words,
)


def test_auto_reply_detects_business_autoresponders() -> None:
    # thread 2503, verbatim — Stepan answered this robot and reset the follow-up cycle
    assert is_auto_reply("Halo, terima kasih sudah menghubungi kami. Kami sudah menerima "
                         "pesan Anda dan menghargai upaya Anda menghubungi kami.")
    assert is_auto_reply("Ini pesan otomatis, kami akan segera balas secepatnya")
    assert is_auto_reply("Thank you for contacting us! We received your message.")


def test_auto_reply_ignores_a_human_saying_thanks() -> None:
    # a real lead thanking US must never be mistaken for a robot — that would freeze the
    # thread's timer and let the price gate stay shut on someone who actually spoke
    for s in ["makasih kak infonya", "terima kasih ya kak 😊", "oke terima kasih",
              "terima kasih sudah menjelaskan"]:
        assert not is_auto_reply(s), s


def test_auto_reply_does_not_count_as_the_lead_speaking() -> None:
    ad = "💻 Ceritakan lebih detail tentang program kursusnya"
    auto = "Halo, terima kasih sudah menghubungi kami. Kami sudah menerima pesan Anda."
    # clicker + their robot = still nobody has spoken → price stays locked
    assert not lead_spoke_own_words([_M("in", ad), _M("out", "hai"), _M("in", auto)])
    # …but one real word from the lead flips it
    assert lead_spoke_own_words([_M("in", ad), _M("in", auto), _M("in", "berapa harganya?")])


# ─── discover before price (thread 4086) ───

from app.modules.conversation.situations import DISCOVER_BEFORE_PRICE_NUDGE  # noqa: E402


def test_discover_before_price_fires_for_engaged_lead_without_a_pain() -> None:
    # lead picked a format ("online dari rumah") — engaged, but no pain and no price question
    got = _pick("online dari rumah", n=2)
    assert got is not None and DISCOVER_BEFORE_PRICE_NUDGE in got


def test_discover_before_price_yields_to_a_direct_question() -> None:
    # they DID ask — answer-first wins, neither discover-first nor the price-framing steer
    # (a plain non-price question is a clean answer-first, no framing variant)
    got = _pick("jadwalnya hari apa kak?", n=2)
    assert ANSWER_FIRST_NUDGE in got
    assert DISCOVER_BEFORE_PRICE_NUDGE not in got and ANSWER_PRICE_NO_PAIN_NUDGE not in got
    # a price ask must also never fall back to the discover-first steer — they asked
    assert DISCOVER_BEFORE_PRICE_NUDGE not in _pick("berapa biayanya kak?", n=2)


def test_discover_before_price_yields_once_a_pain_is_captured() -> None:
    # pain on record → need-payoff owns it, not the pre-pain discovery steer
    needs = NeedsProfile(pains=["followers stuck"], gains=[])
    got = _pick("oke kak", needs=needs, n=2)
    assert NEED_PAYOFF_NUDGE in got and DISCOVER_BEFORE_PRICE_NUDGE not in got


def test_soft_no_detects_the_fikir_spelling_and_suffixes() -> None:
    # thread 2689: "Nanti saya fikirkan lagi ya kak" matched nothing — 'fikir' (f-spelling)
    # with the -kan suffix — so the lead was dormant-ed instead of snoozed
    for s in ["Nanti saya fikirkan lagi ya kak", "saya pikirkan dulu ya",
              "aku fikir dulu deh", "mikirin dulu ya kak"]:
        assert _SOFT_NO_RE.search(s), s


def test_auto_reply_detects_the_english_away_message() -> None:
    # the exact phrasing the decision contract calls out — the detector missed it, so an
    # English auto-responder was still treated as the lead speaking
    assert is_auto_reply("Thanks for your message, we'll get back to you")
    assert is_auto_reply("Thank you for your message, we will get back to you shortly")
    assert not is_auto_reply("makasih kak, message nya udah aku baca")


# ─── price question with no pain on record (the 71%-ghost leak) ───

from app.modules.conversation.situations import ANSWER_PRICE_NO_PAIN_NUDGE  # noqa: E402


def test_price_question_without_a_pain_gets_the_framed_answer() -> None:
    got = _pick("berapa biayanya kak?", n=2)
    assert ANSWER_PRICE_NO_PAIN_NUDGE in got
    assert "DP" in got  # must lead with the smallest step, not the total


def test_price_question_once_a_pain_is_known_uses_plain_answer_first() -> None:
    # pain on record → the number has something to stand against; normal answer-first
    needs = NeedsProfile(pains=["followers stuck"], gains=["naik order"])
    got = _pick("berapa biayanya kak?", needs=needs, n=2)
    assert ANSWER_FIRST_NUDGE in got and ANSWER_PRICE_NO_PAIN_NUDGE not in got


# (merged into test_discover_before_price_yields_to_a_direct_question and
#  test_combo_question_from_tight_budget_answers_with_cheap_entry — were exact duplicates)


def test_soft_no_catches_polite_not_interested() -> None:
    # thread 2949: "maaf belum tertarik" got a discovery question + a follow-up over the no
    for s in ["Makasih Kak tawaran nya, maaf belum tertarik 🙏", "belum tertarik",
              "tidak tertarik kak", "gak tertarik", "belum minat", "maaf belum berminat",
              "ga minat kak"]:
        assert _SOFT_NO_RE.search(s), s


def test_soft_no_ignores_positive_interest() -> None:
    for s in ["saya tertarik banget", "tertarik kak mau daftar", "minat dong",
              "iya berminat sekali kak"]:
        assert not _SOFT_NO_RE.search(s), s


# ─── present-and-close: discovery done → move, capture WA (levers 1+4) ───

from app.modules.conversation.situations import PRESENT_AND_CLOSE_NUDGE  # noqa: E402


def test_present_and_close_fires_once_pain_and_gain_are_captured() -> None:
    needs = NeedsProfile(pains=["followers stuck"], gains=["dapat klien"])
    got = _pick("oke kak", needs=needs, n=3)
    assert got is not None and PRESENT_AND_CLOSE_NUDGE in got
    assert "WA" in got  # must capture contact so a ghosting lead stays reachable


def test_present_and_close_yields_to_a_live_question() -> None:
    # they asked something → answer-first owns the turn, not the close nudge
    needs = NeedsProfile(pains=["followers stuck"], gains=["dapat klien"])
    got = _pick("jadwalnya hari apa kak?", needs=needs, n=3)
    assert ANSWER_FIRST_NUDGE in got and PRESENT_AND_CLOSE_NUDGE not in got


def test_present_and_close_does_not_fire_before_discovery_is_done() -> None:
    # pain but no gain → still need-payoff, not the close
    got = _pick("oke kak", needs=NeedsProfile(pains=["takut gagal"], gains=[]), n=2)
    assert PRESENT_AND_CLOSE_NUDGE not in (got or "")
    # nothing captured → discover-first, not the close
    got2 = _pick("oke kak", needs=NeedsProfile(), n=2)
    assert PRESENT_AND_CLOSE_NUDGE not in (got2 or "")


# ─── Indonesian money shorthand: '4jta' is money, never years (thread 4045) ───

from app.modules.conversation.situations import AMOUNT_SHORTHAND_RE  # noqa: E402


def test_amount_shorthand_detects_bare_money_answers() -> None:
    for s in ["4jta", "4 jt", "500rb", "1,5 juta", "Rp 300 ribu", "2.5jt"]:
        assert AMOUNT_SHORTHAND_RE.match(s), s


def test_amount_shorthand_ignores_non_money() -> None:
    # a longer sentence has context of its own — the hint is only for the BARE amount
    for s in ["4 tahun", "umur 15", "kelas 12", "4jta per bulan target saya",
              "berapa juta?", "oke kak"]:
        assert not AMOUNT_SHORTHAND_RE.match(s), s


def test_amount_hint_rides_on_the_nudge() -> None:
    got = _pick("4jta", n=2)
    assert got is not None and "AMOUNT OF MONEY" in got and "4jta" in got
    assert "NOT years" in got


# ─── shared ad-caption is not the lead speaking (bench 4045/3917/2802) ───


_AD_CAPTION = ("📷 itstep_jakarta · itstep_jakarta Masih scroll tapi belum menghasilkan? 👀 "
               "Saatnya upgrade skill di Regular Program Social Media Marketing 🚀 "
               "🔗 https://fb.itstep.org/Mwbel")


def test_shared_ad_caption_does_not_count_as_lead_speaking() -> None:
    # the full ad copy shared back is a click on OUR content, not the lead's words → no price
    assert not lead_spoke_own_words([_M("in", _AD_CAPTION)])
    assert not lead_spoke_own_words([_M("in", "📷 itstep_jakarta")])  # bare share too
    # …but one real word flips it
    assert lead_spoke_own_words([_M("in", _AD_CAPTION), _M("in", "berapa harganya kak?")])


def test_ad_caption_first_turn_gets_the_ad_opener_not_a_price() -> None:
    got = pick_nudge(lead_type=None, dialog=[_M("in", _AD_CAPTION)], last_txt=_AD_CAPTION,
                     stored_needs=NeedsProfile(), inbound_count=1)
    assert got == AD_OPENER_NUDGE  # ad opener owns it — never a price


def test_materials_question_is_answerable() -> None:
    for s in ["Saat di kelas apa aja materi yang di kasih", "kurikulumnya gimana",
              "apa aja yang dipelajari", "modul apa aja kak"]:
        assert _is_answerable_question(s), s

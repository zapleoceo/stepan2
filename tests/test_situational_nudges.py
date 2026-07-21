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


def test_followup_angle_ladder_is_attempt_specific() -> None:
    from app.modules.conversation.situations import followup_angle
    a1, a2, a3, a4 = (followup_angle(i) for i in range(4))
    assert "follow-up #1" in a1 and "re-open" in a1.lower()
    assert "follow-up #2" in a2 and "proof" in a2.lower()
    assert "follow-up #3" in a3 and "barrier" in a3.lower()
    assert "follow-up #4" in a4 and "close" in a4.lower()
    # every attempt past the fourth reuses the graceful-close rung, never crashes
    assert "close" in followup_angle(9).lower() and "follow-up #10" in followup_angle(9)


def test_postpone_days_parses_named_recontact_times() -> None:
    from app.modules.conversation.situations import postpone_days
    assert postpone_days("oke kak bulan depan aja ya") == 30
    assert postpone_days("2 minggu lagi ya") == 14
    assert postpone_days("besok aku kabari") == 1
    assert postpone_days("3 hari lagi") == 3
    assert 2 <= postpone_days("abis gajian deh kak") <= 30  # payday window, date-dependent
    assert postpone_days("nanti aja kak") is None            # vague → default snooze
    assert postpone_days("berapa harganya?") is None


def test_menu_reply_tolerates_decorated_digits() -> None:
    from app.modules.conversation.situations import MENU_REPLY_RE
    # live 4531: '2 kak' fell through and the lead's answered choice got re-asked
    for s in ["2 kak", "2", "no 2", "yang 3 min", "1️⃣"]:
        assert MENU_REPLY_RE.match(s), s
    for s in ["2 juta", "08123456", "saya pilih kursus"]:
        assert not MENU_REPLY_RE.match(s), s


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
              "diskusi sama orang tua dulu ya", "ga dulu deh", "nabung dulu",
              # 'tidak/gak jadi' = backing out (thread 2811)
              "maaf KA tidak jadi", "saya tidak jadi", "gak jadi kak",
              # warm-postponer forms with the 'dlu' chat abbreviation (thread 4520)
              "aku simpen dlu deh ini", "nanti biar aku mikir mikir dlu ya",
              "aku cuman mau ngamanin informasi dulu",
              # graceful-close forms (thread 4520 second pass)
              "nanti ya kak aku sembari kerja jadinya ngumpulin duitnya dulu",
              "nanti kalo aku ngerasa udah pengen banget aku langsung kabarin kakanya",
              # blunt slang refusals the bot pitched over (thread 4280, a schoolkid)
              "GA USAH", "ga usah kak", "gausah", "Ga ikutan gw", "gak ikutan",
              "ogah ah", "G dulu makasih"]:
        assert _SOFT_NO_RE.search(s), s


def test_soft_no_ignores_engaged_replies() -> None:
    for s in ["iya kak mau daftar", "boleh minta linknya?", "oke lanjut",
              "jadwalnya kapan kak?", "saya tertarik banget",
              # 'jadi' as 'so/then' must NOT read as the 'tidak jadi' refusal
              "jadi kapan kelasnya?", "jadi gimana ka",
              # 'simpanan' / 'mikir positif' must not trip the postponer forms
              "simpanan aku cukup kok", "mikir positif dong",
              # 'kabarin kalo ada promo' / 'kalo bayar pakai apa' are engaged, not a close
              "kabarin aku kalo ada promo ya", "kalo bayar pakai apa kak",
              # 'mau ikutan' is a YES; 'males kerja gini' is a career PAIN, not a course
              # refusal — neither may read as a blunt-slang decline
              "mau ikutan kak", "aku ikutan ya", "males kerja gini terus",
              "udah males sama kerjaan sekarang"]:
        assert not _SOFT_NO_RE.search(s), s


def test_low_budget_detects_money_signals() -> None:
    for s in ["ga ada modal kak", "belum punya uang", "mahal banget",
              "kemahalan kak", "gratisan aja bisa?", "masih nganggur",
              "butuh kerja bukan sertifikat", "ga ada ongkos ke sana",
              # thread 4545: "can't afford" with words between the negation and 'bayar'
              "saya ga punya buat bayar biaya nya", "ga ada buat bayar",
              "duitnya ga cukup buat bayar", "ga sanggup bayar"]:
        assert _LOW_BUDGET_RE.search(s), s


def test_low_budget_ignores_neutral() -> None:
    for s in ["harganya berapa kak?", "bisa dicicil ga?", "ada diskon ga",
              "mau bayar sekarang", "gimana cara bayar", "bisa bayar cicilan ga?"]:
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
    NO_TIME_NUDGE,
    OBJECTION_HANDLE_NUDGE,
    SOFT_NO_NUDGE,
    SOFT_NO_WITH_QUESTION_NUDGE,
    pick_nudge,
)

_SPOKE = [_M("in", "aku mau belajar coding kak")]  # the lead has real words on record


def _pick(last_txt: str, needs: NeedsProfile | None = None, n: int = 2) -> str | None:
    return pick_nudge(lead_type="warm", dialog=[*_SPOKE, _M("in", last_txt)],
                      last_txt=last_txt, stored_needs=needs or NeedsProfile(),
                      inbound_count=n)


def _pick_repeat(prior_no: str, last: str) -> str | None:
    # a dialog where the lead already soft-declined once (prior_no) and does again (last)
    dialog = [_M("in", "mau belajar coding"), _M("in", prior_no),
              _M("out", "boleh tau yang bikin ragu?"), _M("in", last)]
    return pick_nudge(lead_type="warm", dialog=dialog, last_txt=last,
                      stored_needs=NeedsProfile(), inbound_count=3)


def test_first_soft_no_works_the_objection_then_repeat_eases_off() -> None:
    # FIRST soft-no this conversation → surface + handle the objection, don't capitulate
    # (thread 2949: 'belum tertarik' got an instant give-up and the sale was lost)
    assert OBJECTION_HANDLE_NUDGE in _pick("maaf belum tertarik kak")
    assert OBJECTION_HANDLE_NUDGE in _pick("nanti dulu deh kak, tapi berapa sih harganya?")
    # a SECOND soft-no (one already handled) eases off for real
    assert SOFT_NO_NUDGE in _pick_repeat("nanti dulu deh", "belum tertarik")
    assert SOFT_NO_WITH_QUESTION_NUDGE in _pick_repeat(
        "nanti dulu", "pikir dulu, tapi berapa harganya?")


def test_objection_trailed_by_filler_still_handled_not_pitched_over() -> None:
    # thread 4573: 'Nanti aja lagi galau gue' then 'Maaf yaaaa' — last_txt is the apology, but
    # the soft-no earlier in the SAME turn must still route to objection-handling.
    dialog = [_M("in", "mau belajar coding"), _M("out", "yuk Kak"),
              _M("in", "Nanti aja lagi galau gue"), _M("in", "Maaf yaaaa")]
    got = pick_nudge(lead_type="warm", dialog=dialog, last_txt="Maaf yaaaa",
                     stored_needs=NeedsProfile(), inbound_count=3)
    assert OBJECTION_HANDLE_NUDGE in (got or "")
    # but a fresh question as the LAST message still gets answered, not the objection nudge,
    # even if an earlier turn message soft-declined (the lead re-engaged)
    dialog2 = [_M("in", "mau belajar coding"), _M("out", "yuk Kak"),
               _M("in", "nanti dulu deh"), _M("in", "eh tapi berapa biayanya kak?")]
    got2 = pick_nudge(lead_type="warm", dialog=dialog2, last_txt="eh tapi berapa biayanya kak?",
                      stored_needs=NeedsProfile(), inbound_count=3)
    assert OBJECTION_HANDLE_NUDGE not in (got2 or "")


def test_premature_contact_ask_flags_cold_wa_grab_spares_warm() -> None:
    from app.modules.conversation.situations import premature_contact_ask
    cold = dict(has_pains=False, has_phone=False, ready=False)
    # thread 4615: menu tap → 'give me your WhatsApp' on a cold lead
    assert premature_contact_ask("boleh minta nomor WhatsApp-nya ya Kak?", "1", **cold)
    assert premature_contact_ask("boleh minta nomornya, Kak?", "2", **cold)
    # warm signals spare it: a price/pay/buying signal this turn, a captured pain, a phone
    assert not premature_contact_ask("boleh minta nomor WA?", "berapa biayanya kak?", **cold)
    assert not premature_contact_ask("boleh minta nomor WA?", "mau daftar dong", **cold)
    assert not premature_contact_ask(
        "boleh minta nomor WA?", "1", has_pains=True, has_phone=False, ready=False)
    # a reply with no contact ask is never flagged
    assert not premature_contact_ask("Keren Kak! Mau bikin aplikasi buat apa?", "1", **cold)
    # ready=true no longer bypasses: the model over-sets it before a phone is in hand (thread
    # 4725 — ready=true on 'lihat contohnya' with empty discovery got a phone-grab)
    assert premature_contact_ask(
        "boleh minta nomor WA?", "jasa bisa lihat contohnya",
        has_pains=False, has_phone=False, ready=True)
    # an OPEN OBJECTION must be handled first — even with a captured pain (thread 4715: the
    # 'pain' was a competence worry 'belum paham SQL', re-voiced, and got a phone-grab)
    assert premature_contact_ask(
        "boleh minta nomor WA?", "belum paham sql nya kak",
        has_pains=True, has_phone=False, ready=True, has_open_objection=True)
    # a fresh answerable question must be answered first, not swapped for a number
    assert premature_contact_ask(
        "boleh minta nomor WA?", "kelasnya online ga kak?",
        has_pains=True, has_phone=False, ready=True)
    # a phone already in hand is never a 'cold grab', whatever else is true
    assert not premature_contact_ask(
        "boleh minta nomor WA?", "1", has_pains=False, has_phone=True, ready=False)
    # the ad prefill carries 'biaya' but is a BUTTON CLICK, not a warm price question — a
    # phone-grab at a silent ad-clicker is still premature (thread 4755)
    assert premature_contact_ask(
        "boleh minta nomor WhatsApp-nya ya Kak?",
        "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?", **cold)


def test_fake_serendipity_regex_flags_canned_openers() -> None:
    from app.modules.conversation.situations import FAKE_SERENDIPITY_RE
    assert FAKE_SERENDIPITY_RE.search("Eh Kak, kebetulan nih baru aja ada alumni yang...")
    assert FAKE_SERENDIPITY_RE.search("baru aja ada project alumni yang keren")
    assert FAKE_SERENDIPITY_RE.search("eh iya baru inget soal kelas Kakak")
    # a plain, concrete opener is fine
    assert not FAKE_SERENDIPITY_RE.search("Kak, program Data Analyst durasinya 9 bulan ya")


def test_no_time_objection_gets_the_grounded_schedule_reframe() -> None:
    # thread 4062: 'waktunya padet kak belum ada waktu' got a capitulation, not a reframe.
    # The TIME objection must route to the schedule reframe, not generic soft-no capitulation.
    assert NO_TIME_NUDGE in (_pick("waktunya padet kak belum ada waktu") or "")
    assert NO_TIME_NUDGE in (_pick("aku lagi sibuk banget kak") or "")
    assert NO_TIME_NUDGE in (_pick("gak sempat kak") or "")
    # a positive 'free time' and a schedule question must NOT read as the objection
    assert NO_TIME_NUDGE not in (_pick("kapan waktunya kelas kak?") or "")


def test_no_time_repeat_eases_off_instead_of_hammering() -> None:
    dialog = [_M("in", "mau belajar"), _M("in", "aku sibuk kak"),
              _M("out", "cuma 2 sesi malam per minggu kok"), _M("in", "tetep gak sempat sih")]
    got = pick_nudge(lead_type="warm", dialog=dialog, last_txt="tetep gak sempat sih",
                     stored_needs=NeedsProfile(), inbound_count=3)
    assert NO_TIME_NUDGE not in (got or "")  # second time objection → ease off, not re-reframe
    assert SOFT_NO_NUDGE in (got or "")


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


def test_trust_doubt_gets_legitimacy_answer_not_menu() -> None:
    from app.modules.conversation.situations import TRUST_DOUBT_NUDGE, TRUST_DOUBT_RE
    # thread 4435: 'Apakah ini real' was answered with the clarify menu
    for s in ["Apakah ini real", "takut scam soalnya", "ini resmi kah?", "aman ga sih"]:
        assert TRUST_DOUBT_RE.search(s), s
    for s in ["prediksi market realtime bisa?", "berapa harganya"]:
        assert not TRUST_DOUBT_RE.search(s), s
    assert TRUST_DOUBT_NUDGE in _pick("apakah ini beneran resmi kak?")

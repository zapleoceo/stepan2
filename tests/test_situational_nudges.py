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
              "ini gratis atau ada biaya nya kak"]:
        assert _answer_first_fires(s), s


def test_answer_first_never_fires_on_ad_prefill() -> None:
    # the ad button click mentions 'biaya' but must NOT get a price — _AD_OPENER_NUDGE owns it
    for s in ["Halo, saya ingin tahu detail program SMM dan biaya kursusnya 😊",
              "💻 Ceritakan lebih detail tentang program kursusnya",
              "🐍 Ceritakan lebih detail tentang program kursus Python"]:
        assert not _answer_first_fires(s), s


def test_answer_first_ignores_non_questions() -> None:
    for s in ["oke makasih", "iya kak", "Mantap"]:
        assert not _answer_first_fires(s), s


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
    assert got == SOFT_NO_WITH_QUESTION_NUDGE
    # a plain stall without a question keeps the pure soft-no handling
    assert _pick("nanti dulu deh kak") == SOFT_NO_NUDGE


def test_combo_question_from_tight_budget_answers_with_cheap_entry() -> None:
    # 'ga ada modal, berapa biayanya?' — honest number + the affordable entry beside it
    got = _pick("ga ada modal kak, berapa biayanya?")
    assert got == ANSWER_FIRST_TIGHT_BUDGET_NUDGE
    assert _pick("berapa biayanya kak?") == ANSWER_FIRST_NUDGE


def test_need_payoff_respects_discovery_cap() -> None:
    # pain with no gain asks for the payoff — but only until the cap releases the lead;
    # past it the discovery-cap nudge presents on what we have (no endless interrogation)
    needs = NeedsProfile(pains=["takut gagal"], gains=[])
    assert _pick("oke kak", needs=needs, n=2) == NEED_PAYOFF_NUDGE
    past_cap = _pick("oke kak", needs=needs, n=DISCOVERY_TURN_CAP + 1)
    assert past_cap is not None and past_cap != NEED_PAYOFF_NUDGE  # falls to discovery-cap

"""Situational-nudge detectors (soft-no / low-budget / minor) — deterministic triggers that
carry the Jakarta-methodology rules the model followed unreliably at prompt scale."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.modules.conversation.reply import (  # noqa: E402
    _AD_TEMPLATE_RE,
    _LOW_BUDGET_RE,
    _MINOR_RE,
    _SOFT_NO_RE,
    _is_answerable_question,
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

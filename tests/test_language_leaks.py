"""Nine places shipped Indonesian — or IT STEP's own facts — to every branch that ever ran.

The prompt contract was fixed on 2026-08-03 (test_contract_language_isolation), and everyone
assumed that was the whole leak. It was not. A hand-off closing, the money gate's hold-line,
the date annotations inside the cached prefix, the discovery extractor's paraphrase target,
the money gate's own correction, the public-comment fallback and the CRM policy block are all
written by CODE, appended AFTER the model's reply or injected around it — so the reply itself
reads correct in the lead's language and the canned line underneath it does not.

Every test here pins the same two things: branch 1 gets exactly the bytes it gets today, and
nobody else gets Bahasa Indonesia by default.
"""
from __future__ import annotations

import os
import re
from datetime import date

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.modules.conversation.canned import (  # noqa: E402
    comment_fallback,
    comment_persona,
    escalation_hold,
    manager_closing,
    ready_closing,
)
from app.modules.conversation.dates import (  # noqa: E402
    EXPIRED_NOTE,
    annotate_dates,
    strip_date_annotations,  # noqa: E402
)
from app.modules.conversation.discovery import _system  # noqa: E402
from app.modules.conversation.money_gate import money_correction  # noqa: E402
from app.modules.crm.policy import crm_state_block  # noqa: E402

# Words that can only belong in an Indonesian conversation. Same list the contract test uses,
# plus the ones the canned lines carry.
_ID_ONLY = ("Kak", "Kakak", "aku", "tim kami", "jam kerja", "WIB", "Terima kasih", "Halo")
_OTHER_LANGS = ("en", "ru", "de", "vi", "")


def _has_indonesian(text: str) -> bool:
    return any(word in text for word in _ID_ONLY)


# ── (a) the money gate's hold-line ────────────────────────────────────────────

def test_the_hold_line_is_unchanged_for_the_indonesian_branch() -> None:
    """Branch 1 sends this on every unfixable money escalation. Pinned byte for byte."""
    assert escalation_hold("id") == (
        "Kakak, bentar ya - aku cek dulu ke tim supaya infonya pas dan akurat. "
        "Nanti dibantu langsung di jam kerja (Senin-Jumat, 09.00-18.00 WIB) 🙏")


@pytest.mark.parametrize("lang", _OTHER_LANGS)
def test_no_branch_gets_the_hold_line_in_bahasa(lang: str) -> None:
    assert not _has_indonesian(escalation_hold(lang))


# ── (b) the three hand-off closings ───────────────────────────────────────────

def test_the_manager_closings_are_unchanged_for_the_indonesian_branch() -> None:
    assert manager_closing("id", has_phone=True) == (
        "Terima kasih ya Kak! Untuk ini tim kami yang akan bantu langsung - nanti dihubungi "
        "via telepon atau WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) ya 🙏")
    assert manager_closing("id", has_phone=False) == (
        "Terima kasih ya Kak! Untuk ini tim kami yang akan bantu langsung. Boleh minta nomor "
        "telepon/WhatsApp Kakak? Biar tim bisa hubungi di jam kerja (Senin-Jumat, "
        "09.00-18.00 WIB) ya 🙏")


def test_the_ready_closing_is_unchanged_for_the_indonesian_branch() -> None:
    assert ready_closing("id") == (
        "Siap Kak! Pendaftaran Kakak aku teruskan ke tim ya - nanti dihubungi via telepon "
        "atau WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) untuk langkah pembayaran "
        "& jadwal 🙏")


@pytest.mark.parametrize("lang", _OTHER_LANGS)
def test_no_branch_gets_a_closing_in_bahasa(lang: str) -> None:
    for line in (manager_closing(lang, has_phone=True),
                 manager_closing(lang, has_phone=False),
                 ready_closing(lang)):
        assert not _has_indonesian(line)


def test_the_no_phone_closing_still_asks_for_a_number_in_every_language() -> None:
    """The whole point of this variant (thread 452): it is the last chance to get a contact
    before the bot goes silent. A translation that drops the ask is a broken translation."""
    assert "WhatsApp" in manager_closing("en", has_phone=False)
    assert "?" in manager_closing("en", has_phone=False)
    assert "WhatsApp" in manager_closing("ru", has_phone=False)
    assert "?" in manager_closing("ru", has_phone=False)


def test_an_unknown_language_falls_back_to_english_not_indonesian() -> None:
    """The rule for the whole table: a branch we have no translation for reads English. That
    is a small failure; reading Bahasa Indonesia ends the conversation."""
    assert ready_closing("sw") == ready_closing("en")
    assert escalation_hold(None) == escalation_hold("en")


# ── (c) the date annotations, which land in the CACHED PREFIX ─────────────────

_TODAY = date(2026, 7, 23)  # a Thursday


def test_the_indonesian_annotation_is_byte_for_byte_what_production_reads() -> None:
    """This text goes into messages[0], the broker's prompt-cache anchor for branch 1. One
    changed byte is a cache miss on every lead of the branch, and a changed instruction to a
    model that has been selling against it for weeks."""
    assert annotate_dates("Demo Event: Sabtu, 8 Agustus 2026", _TODAY) == \
        "Demo Event: Sabtu, 8 Agustus 2026 [hari Sabtu, 16 hari lagi]"
    assert annotate_dates("batch 11 Juli 2026", _TODAY) == \
        f"batch 11 Juli 2026 [{EXPIRED_NOTE}]"
    assert annotate_dates("mulai 24 Juli 2026", _TODAY) == \
        "mulai 24 Juli 2026 [hari Jumat, besok]"
    assert annotate_dates("mulai 23 Juli 2026", _TODAY) == \
        "mulai 23 Juli 2026 [hari Kamis, hari ini]"


def test_an_english_branch_reads_its_dates_in_english() -> None:
    """An English branch used to be handed 'hari Sabtu, 16 hari lagi' inside its own English
    knowledge base, and then asked not to translate dates."""
    out = annotate_dates("Demo Event: August 8, 2026", _TODAY, "en")
    assert out == "Demo Event: August 8, 2026 [Saturday, in 16 days]"
    assert "hari" not in out


def test_an_expired_date_is_flagged_in_english_too() -> None:
    """The rule this exists for — never offer a batch that already ran — cannot be Indonesian
    only, or an English branch invites people to a session nobody will hold."""
    out = annotate_dates("Next group: July 19, 2026", date(2026, 7, 25), "en")
    assert "ALREADY PAST" in out and "SUDAH LEWAT" not in out


def test_an_unlisted_language_gets_english_dates_not_indonesian() -> None:
    assert annotate_dates("acara 8 Agustus 2026", _TODAY, "vi") == \
        annotate_dates("acara 8 Agustus 2026", _TODAY, "en")


@pytest.mark.parametrize("lang", ["id", "en"])
def test_the_stripper_removes_every_locale_it_can_write(lang: str) -> None:
    """clean_reply is the last thing between an annotation and the lead's screen: the model
    copies our brackets into its own text (sim, 27.07). A locale whose markers the stripper
    does not know ships them verbatim."""
    annotated = annotate_dates("Demo Event: 8 Agustus 2026", _TODAY, lang)
    assert strip_date_annotations(annotated) == "Demo Event: 8 Agustus 2026"


def test_annotating_twice_changes_nothing_in_any_locale() -> None:
    """The context is memoized and reused across a turn's rewrites — brackets must not stack."""
    once = annotate_dates("Sabtu, 8 Agustus 2026", _TODAY, "en")
    assert annotate_dates(once, _TODAY, "en") == once


# ── (d) the discovery extractor's paraphrase target ───────────────────────────

def test_the_extractor_still_paraphrases_into_indonesian_for_branch_one() -> None:
    assert "Paraphrase tightly into Indonesian." in _system("id")


@pytest.mark.parametrize("lang,expected", [("en", "English"), ("ru", "Russian")])
def test_the_extractor_writes_the_dossier_in_the_branch_language(
        lang: str, expected: str) -> None:
    """The dossier it produces is read back into the NEXT turn's reply prompt and shown to a
    human in the admin UI. Forcing Indonesian there put Bahasa into an English branch's
    prompt on every single turn."""
    system = _system(lang)
    assert f"Paraphrase tightly into {expected}." in system
    assert "into Indonesian." not in system


# ── (e) the money-gate correction and IT STEP's own catalogue ─────────────────

def test_the_correction_is_byte_for_byte_what_branch_one_sends_today() -> None:
    """The rewrite instruction the money gate hands back on every trip. Pinned whole, because
    splitting the tenant clause out of the middle of a sentence is exactly the kind of edit
    that silently loses a word."""
    assert money_correction("id") == (
        "[System: your draft states a figure/link OR offers a service/material that is NOT in "
        "the knowledge base: {issues}. Rewrite the SAME message keeping its intent and warmth, "
        "but state only figures and links that appear in the knowledge base above, and offer "
        "ONLY what the school actually provides — the only free thing you may offer is a "
        "campus visit; the Demo Event is a paid offer. Do NOT invent a consultation, session, "
        "or a document you'll prepare. If you don't have the fact, say what you do know and "
        "offer to confirm with the team — do not go silent and do not hand the lead off.]")


@pytest.mark.parametrize("lang", _OTHER_LANGS)
def test_no_other_branch_is_taught_it_step_s_catalogue(lang: str) -> None:
    """Naming a "Demo Event" to a branch that has never held one teaches the model an offer
    that does not exist — and the rewrite it produces then invents around it."""
    text = money_correction(lang)
    assert "Demo Event" not in text
    assert "campus visit" not in text


@pytest.mark.parametrize("lang", ["id", "en", "ru"])
def test_the_correction_still_demands_a_rewrite_never_a_retreat(lang: str) -> None:
    """v2's corrections let the model fall back to 'I'll check with the team', which is how it
    learned to go quiet on answerable questions. No translation may lose that."""
    text = money_correction(lang).format(issues="x")
    assert "do not go silent" in text and "do not hand the lead off" in text
    assert "{" not in text and "}" not in text  # the issues placeholder is the only one


# ── (h) the public-comment persona and fallback ───────────────────────────────

def test_the_comment_prompt_still_calls_itself_minstep_on_branch_one() -> None:
    assert comment_persona("id") == "MinStep"
    assert comment_fallback("id") == \
        "Halo Kak! 😊 Boleh DM aku ya biar aku bantu jelasin lengkap 🙏"


@pytest.mark.parametrize("lang", _OTHER_LANGS)
def test_no_tenant_borrows_another_tenant_s_mascot(lang: str) -> None:
    """A name is tenant identity, not language. A demo branch introducing itself as MinStep
    is a different company's assistant answering in public under our client's post."""
    assert comment_persona(lang) != "MinStep"
    assert not _has_indonesian(comment_fallback(lang))


# ── (i) the CRM policy block injected into messages[1] ────────────────────────

_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")


def test_the_crm_block_is_unchanged_for_the_indonesian_branch() -> None:
    """Owner-written Russian, which the model reads fine and which branch 1 has been selling
    against — it stays exactly as it is."""
    assert crm_state_block("wait_call", manager="Rina", when="2026-07-30T10:00:00Z") == (
        "## ЧТО ПРОИСХОДИТ У МЕНЕДЖЕРА (из CRM)\n"
        "Последний контакт менеджера (Rina), 2026-07-30: wait_call.\n"
        "Менеджер уже говорил с этим человеком по телефону и ждёт следующего созвона. "
        "Сам первым не пиши. Если человек написал — отвечай обычно и по делу, не "
        "пересказывай ему, что было на звонке, и не выясняй заново то, что он уже "
        "обсудил с менеджером.")
    block = crm_state_block("result_think", next_contact_at="2026-08-01T00:00:00Z")
    assert block is not None
    assert "Следующий контакт запланирован на 2026-08-01 — до этой даты сам не пиши." in block
    assert "Не дави и не продавай заново." in block


@pytest.mark.parametrize("status", ["wait_call", "result_think", "result_next_enrollment",
                                    "result_fail", "result_event"])
@pytest.mark.parametrize("lang", ["en", "vi", ""])
def test_no_branch_gets_a_russian_paragraph_in_its_prompt(status: str, lang: str) -> None:
    block = crm_state_block(status, manager="Rina", next_contact_at="2026-08-01T00:00:00Z",
                            lang=lang)
    assert block is not None
    assert not _CYRILLIC.search(block)


def test_the_english_block_keeps_the_do_not_write_first_instruction() -> None:
    """The one rule in this block with a send attached to it — a translation that drops it
    turns a scheduled callback into an unsolicited message."""
    block = crm_state_block("wait_call", next_contact_at="2026-08-01T00:00:00Z", lang="en")
    assert block is not None and "do not write first" in block

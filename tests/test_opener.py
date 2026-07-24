"""The first-contact classifier and skeletons — the module that ended the July opener
incidents (threads 4943, 5005, 5019, 5024, 5029, 5031, 5095, 5097): the entry is classified
by code, and the first message is never free-generated."""
from __future__ import annotations

from dataclasses import dataclass

from app.modules.conversation.opener import (
    AD_TAP_OPENER,  # noqa: F401 — re-export surface used by reply.py and older tests
    Entry,
    classify,
    compose_typed_opener,
)

_PREFILL = "Halo! Tertarik kursus. Boleh info jadwal, durasi, dan biaya?"


@dataclass
class _M:
    direction: str
    text: str


def _dlg(*texts: str) -> list[_M]:
    return [_M("in", t) for t in texts]


# ── classification ────────────────────────────────────────────────────────────

def test_pure_prefill_is_ad_silent() -> None:
    fc = classify(_dlg(_PREFILL), "ad_clicktomsg", "AD1")
    assert fc.entry is Entry.AD_SILENT and fc.typed_text == ""


def test_prefill_plus_short_share_header_is_ad_silent() -> None:
    """thread 5095: '📷 itstep_jakarta' + prefill, in either order."""
    assert classify(_dlg("📷 itstep_jakarta", _PREFILL), "ad_clicktomsg", "AD1").entry \
        is Entry.AD_SILENT
    assert classify(_dlg(_PREFILL, "📷 itstep_jakarta"), "ad_clicktomsg", "AD1").entry \
        is Entry.AD_SILENT


def test_bare_ack_from_ad_thread_is_ad_silent() -> None:
    """thread 5097: an ad click whose only message is 'iyaaaa' — nothing to build on."""
    fc = classify(_dlg("iyaaaa"), "ad_clicktomsg", "AD1")
    assert fc.entry is Entry.AD_SILENT


def test_typed_words_on_ad_thread_are_ad_typed() -> None:
    """The composer is editable — real typed words (not matching any prefill family) must
    reach the typed path so answer-first fully applies."""
    fc = classify(_dlg("kelas malamnya ada ga kak? aku kerja sampai jam 6"),
                  "ad_clicktomsg", "AD1")
    assert fc.entry is Entry.AD_TYPED
    assert "kelas malam" in fc.typed_text


def test_edited_prefill_family_text_stays_ad_silent() -> None:
    """thread 4972's wording matches a known prefill FAMILY (AD_TEMPLATE_RE) — regex alone
    can't prove it was typed, so the safe product opener answers it (it names the product and
    invites their goal, which serves a genuine ask too)."""
    fc = classify(_dlg("saya ingin tahu detail program SMM dan biaya kursusnya"),
                  "ad_clicktomsg", "AD1")
    assert fc.entry is Entry.AD_SILENT


def test_walk_in_with_text_is_organic() -> None:
    fc = classify(_dlg("halo, mau tanya kursus data analyst dong"), None, None)
    assert fc.entry is Entry.ORGANIC


def test_story_reply_is_story() -> None:
    assert classify(_dlg("🔥🔥"), "story", None).entry is Entry.STORY


def test_garble_without_ad_is_junk() -> None:
    """thread 5020's opener 'Q)' — near-letterless noise; previously burned a broker call to
    say 'I don't understand'."""
    assert classify(_dlg("Q)"), None, None).entry is Entry.JUNK
    assert classify(_dlg("🙏🙏🙏"), None, None).entry is Entry.JUNK


def test_cyrillic_text_counts_as_typed() -> None:
    """[a-zA-Z] alone filed a Russian first message as letterless junk (thread 452's lead) —
    Unicode letters must count. (reply.decide additionally routes foreign-script turns past
    this module entirely, since the templates are Bahasa-only.)"""
    assert classify(_dlg("Привет, расскажите про курс"), None, None).entry is Entry.ORGANIC


def test_greeting_only_walk_in_is_junk_not_organic() -> None:
    """A bare 'halo' carries nothing to reflect — the clarify template fits better than a
    skeleton with an empty slot."""
    assert classify(_dlg("halo"), None, None).entry is Entry.JUNK


# ── skeleton composition ─────────────────────────────────────────────────────

def test_compose_trims_a_question_out_of_the_slot() -> None:
    """The frame's own question must stay the only one (one-question-per-message)."""
    msg = compose_typed_opener(Entry.AD_TYPED, "SMM Intensive 2 minggu, mau tahu lebih?", None)
    assert msg.count("?") == 1
    assert "mau tahu lebih" in msg  # content kept, question mark stripped


def test_compose_includes_the_lead_name_when_clean() -> None:
    msg = compose_typed_opener(Entry.ORGANIC, "Datanya ada di kurikulum kami.", "Maria")
    assert "Kak Maria" in msg
    assert "MinStep" in msg  # the fixed intro frame survives


def test_compose_caps_slot_length() -> None:
    msg = compose_typed_opener(Entry.AD_TYPED, "x" * 2000, None)
    assert len(msg) < 600


def test_compose_strips_a_second_greeting_from_the_slot() -> None:
    """Live sim (branch 8): the slot opened with its own 'Halo Kak, senang banget…' despite
    SLOT_SYSTEM's no-greeting rule — the frame already greets, so the clause is stripped."""
    msg = compose_typed_opener(
        Entry.ORGANIC, "Halo Kak, senang banget bisa bantu! Vibe Coding cocok banget.", None)
    assert msg.count("Halo") == 1  # only the frame's own greeting survives
    assert "Vibe Coding cocok" in msg  # the content after the greeting is kept

"""The first contact — classified by CODE, never free-generated.

Every first-message incident of 2026-07 (threads 4943, 5005, 5019, 5024, 5029, 5031, 5095,
5097) shared one root cause: the opener was produced by free LLM generation steered by
scattered prose hints, with deterministic gates catching mistakes AFTER the fact at the cost
of escalating a live lead. Each new entry shape got its own regex patch — an unwinnable race.

This module ends it: the entry is classified deterministically into one of five shapes, and
each shape has a known-good response. Personalization comes from SLOTS (the lead's name, the
ad-mapped product, the lead's own words) — not from letting the model improvise the frame.

  AD_SILENT   ad thread, nothing typed (prefill/shares/acks) → product template, zero LLM
  AD_TYPED    ad thread, lead typed real words → skeleton: intro is fixed, the LLM fills
              ONLY the answer slot (answer-first fully applies)
  ORGANIC     walk-in DM with typed text → skeleton: intro fixed, LLM fills a short
              reflection of their words; the deep-discovery question is fixed
  STORY       reply to our story → light template
  JUNK        emoji/garble only, no ad context → clarify template, zero LLM

Only AD_TYPED and ORGANIC call the broker at all, and only for a bounded slot — the model
cannot pitch, quote, or restart discovery from inside a slot."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from .signals import AD_TEMPLATE_RE, ANY_POST_SHARE_RE

logger = logging.getLogger(__name__)

# Any Unicode letter — [a-zA-Z] alone silently classified a Cyrillic first message as
# letterless junk (a Russian-speaking lead like thread 452 would have gotten the Bahasa
# clarify template). Note the templates below are Bahasa-only regardless: reply.decide gates
# this whole module on the lead writing in the branch's own script.
_LETTER_RE = re.compile(r"[^\W\d_]")
_MIN_TYPED_LETTERS = 3  # "Q)" (thread 5020) is noise, not a message to reflect
# IG's attachment placeholder in its SHORT form — icon + handle ("📷 itstep_jakarta",
# thread 5095). A lead's own typed text never begins with these icons.
_SHARE_ICON_RE = re.compile(r"^[📷🎬📖🎥🎞👤]")
_BARE_ACK_RE = re.compile(
    r"^(?:iya*|ya+|ok(?:e+)?|okok|sip|baik|siap|oh|hmm?|halo+|hai+|hi|p)"
    r"\b[\s\W]*(?:kak(?:ak)?)?[\s\W]*$",
    re.IGNORECASE)


class Entry(Enum):
    AD_SILENT = "ad_silent"
    AD_TYPED = "ad_typed"
    ORGANIC = "organic"
    STORY = "story"
    JUNK = "junk"


@dataclass(frozen=True)
class FirstContact:
    entry: Entry
    typed_text: str  # the lead's own words, empty for silent entries


def classify(dialog: list, lead_source: str | None, ad_id: str | None) -> FirstContact:
    """Deterministic entry classification for a first turn (no outbound yet)."""
    texts = [(m.text or "").strip() for m in dialog if m.direction == "in"]
    texts = [t for t in texts if t]
    typed = [t for t in texts if _is_typed(t)]
    # A prefill match is ad evidence in itself — IG drops the referral metadata often enough
    # (thread 4500: an unrecognized prefill family arrived with no ad_id/lead_source at all)
    # that the fixed button text is the more reliable signal of the two.
    from_ad = (lead_source == "ad_clicktomsg" or bool(ad_id)
               or any(AD_TEMPLATE_RE.match(t) for t in texts))
    if from_ad:
        if typed:
            return FirstContact(Entry.AD_TYPED, " ".join(typed))
        return FirstContact(Entry.AD_SILENT, "")
    if lead_source == "story":
        return FirstContact(Entry.STORY, " ".join(typed))
    if typed:
        return FirstContact(Entry.ORGANIC, " ".join(typed))
    return FirstContact(Entry.JUNK, "")


def _is_typed(t: str) -> bool:
    """The lead's OWN words — not a prefill, share artifact, bare ack, or near-letterless
    noise."""
    return not (
        AD_TEMPLATE_RE.match(t) or ANY_POST_SHARE_RE.match(t) or _SHARE_ICON_RE.match(t)
        or _BARE_ACK_RE.match(t) or len(_LETTER_RE.findall(t)) < _MIN_TYPED_LETTERS
    )


# ── deterministic templates (no LLM) ─────────────────────────────────────────

AD_TAP_OPENER = (
    "Halo, aku MinStep dari IT STEP Academy 😊 Seneng banget Kakak tertarik! Biar aku bisa "
    "kasih info yang paling pas, boleh cerita dulu — Kakak lagi cari kursus buat apa nih?"
)
# The 24h sales audit (2026-07-24, 72 threads) measured 61% first-reply silence when the
# opener carried no information. Naming the tapped product + the DP/instalment frame answers
# the spirit of the tap with grounded facts before the one discovery question.
AD_TAP_OPENER_PRODUCT = (
    "Halo, aku MinStep dari IT STEP Academy 😊 Kakak tertarik {title} ya — pilihan seru! "
    "Booking tempatnya cukup DP Rp 500.000, sisanya bisa dicicil tanpa bunga. Biar infonya "
    "pas buat Kakak: rencananya skill ini mau dipakai buat apa nih?"
)
STORY_OPENER = (
    "Halo, aku MinStep dari IT STEP Academy 😊 Makasih udah respon story-nya! "
    "Lagi kepikiran belajar skill baru, atau sekadar penasaran nih?"
)
# Neutral on purpose: it answers a bare greeting ("halo") and unreadable garble ("Qqq b
# nnq", thread 5020) equally well — no "I didn't understand", which reads oddly to someone
# who only said hello.
JUNK_OPENER = (
    "Halo Kak, aku MinStep dari IT STEP Academy Jakarta 😊 Boleh cerita, Kakak lagi cari "
    "info tentang apa ya? Biar aku bisa bantu yang paling pas."
)

# ── skeletons: fixed frame, ONE bounded LLM slot ─────────────────────────────

# The slot generator's whole world: answer the lead's message from the KB facts, in 1-2
# short sentences, nothing else. It cannot introduce itself (the frame does), cannot ask a
# question (the frame does), and a price appears ONLY if the lead's own words asked for one.
SLOT_SYSTEM = (
    "You write ONE short fragment (1-2 sentences, Bahasa Indonesia, warm chat register, no "
    "greeting, no self-introduction, no question at the end) for a sales chat at an IT "
    "school. Use ONLY facts from the knowledge base below; if the needed fact is missing, "
    "say you'll confirm it with the team. If the lead's message asks about money, give the "
    "starting figure with its instalment frame from the knowledge base; otherwise never "
    "mention money.\n\nKNOWLEDGE BASE:\n{kb}\n\nLEAD'S MESSAGE:\n{typed}\n\n"
    "Return ONLY the fragment, no quotes, no JSON."
)

AD_TYPED_FRAME = (
    "Halo{name}, aku MinStep dari IT STEP Academy 😊 {slot} "
    "Biar aku bisa bantu lebih pas — boleh cerita, Kakak mau pakai skill-nya buat apa nanti?"
)
ORGANIC_FRAME = (
    "Halo{name}, aku MinStep dari IT STEP Academy 😊 {slot} "
    "Boleh cerita dikit, apa yang bikin Kakak kepikiran soal ini sekarang?"
)

_SLOT_MAX_CHARS = 320


def compose_typed_opener(entry: Entry, slot: str, lead_name: str | None) -> str:
    """The finished first message: fixed frame + the model's bounded slot.

    A slot that overflows, carries markdown, or smuggles a question in is trimmed — the
    frame's own question must stay the only one (one-question-per-message rule)."""
    cleaned = " ".join((slot or "").split())[:_SLOT_MAX_CHARS].strip()
    if cleaned and cleaned[-1] == "?":
        cleaned = cleaned.rstrip("?").rstrip() + "."
    if cleaned and cleaned[-1] not in ".!…":
        cleaned += "."
    name = f" Kak {lead_name}" if lead_name else ""
    frame = AD_TYPED_FRAME if entry is Entry.AD_TYPED else ORGANIC_FRAME
    return " ".join(frame.format(name=name, slot=cleaned).split())

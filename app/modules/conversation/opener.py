"""The first contact — SILENT entries answered by template, typed ones by the model.

The entry is classified deterministically into one of five shapes. Silent/junk shapes ship
a known-good Bahasa template with zero LLM (anti-ban, zero cost, measured wording); a TYPED
entry (AD_TYPED / ORGANIC) goes to the full free reply pipeline — with the strong chat:sales
chain writing the opener, the old fixed-frame-plus-slot skeleton became scaffolding for a
weaker model and was retired with the scripted path (2026-07-25).

  AD_SILENT   ad thread, nothing typed (prefill/shares/acks) → product template, zero LLM
  STORY       reply to our story, nothing typed → light template
  JUNK        emoji/garble only, no ad context → clarify template, zero LLM
  AD_TYPED / ORGANIC → full pipeline"""
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


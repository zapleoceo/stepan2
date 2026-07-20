"""Triage a public comment before spending a reply on it.

Comments under a post are far noisier than DMs — praise emojis, 'first', spam, tags, abuse.
Answering all of them burns the (deliberately low) hourly cap and looks bot-like, a fast ban
signal. So classify first: reply only to something with a real question or buying intent;
skip the rest; hide outright spam/abuse.

The cost of a public mistake is higher than in a DM (everyone sees it, it screenshots), so
this errs toward SKIP: when in doubt, don't reply publicly.
"""
from __future__ import annotations

import re

from app.modules.conversation.situations import (
    BUYING_SIGNAL_RE,
    PAYMENT_INTENT_RE,
    PRICE_QUESTION_RE,
    is_answerable_question,
)

# Spam / self-promo / contact-harvesting under our post — hide, never engage.
_SPAM_RE = re.compile(
    r"https?://|www\.|\bt\.me/|\bwa\.me/|\bbit\.ly|"
    r"\bfollow\s*(me|back|akun)|\bfolback\b|\bcek\s*(dm|profil|bio)|\bfollow\s*for\s*follow\b|"
    r"\bjual\b|\bjualan\b|\bopen\s*(order|bo|jastip)\b|\bpromo\b.*\bklik\b|"
    r"\bpinjaman\b|\bjudi\b|\bslot\s*gacor\b|\bgacor\b|\bmaxwin\b|\bwd\b.*\bcuan\b",
    re.IGNORECASE)

# Abuse / trolling — hide, don't dignify with a public reply.
_ABUSE_RE = re.compile(
    r"\b(anjing|anjir|bangsat|tolol|goblok|bego|kontol|memek|ngentot|babi|kampret|"
    r"scam|penipu|penipuan|bodoh|bacot)\b",
    re.IGNORECASE)

# Pure noise: 'first', 'hadir', 'up', 'nice', a lone praise word, tags only, or no letters at
# all (emoji/punctuation). Reacting to these is what makes an account read as an auto-bot.
_NOISE_RE = re.compile(
    r"^(first|1st|pertamax|hadir|up|nice|keren|mantap+|bagus|good|ok+|sip|wow|"
    r"gg|amin+|aamiin+|semangat|good\s*luck|goodluck)[\s.!😊👍🔥❤️]*$",
    re.IGNORECASE)
_TAGS_ONLY_RE = re.compile(r"^(\s*@\w+)+\s*$")
_HAS_LETTER_RE = re.compile(r"[a-zA-ZÀ-ɏ]")

# A comment worth a reply even without a '?': explicit interest / how-to-join / DM-me.
_INTEREST_RE = re.compile(
    r"\b(minat|tertarik|mau\s*(daftar|ikut|belajar|gabung|dong|nih|kak)?|"
    r"pengen\s*(ikut|belajar|daftar)|pgn\b|"
    r"info\s*(dong|kak|lengkap)?|caranya|gimana\s*cara|dm\s*(dong|ya|aku|saya|kak)?|"
    r"pm\s*(dong|ya)?|japri|price\s*list|pricelist|\bpl\b|list\s*harga)\b",
    re.IGNORECASE)

# Chat-shorthand questions the standard detector misses (no '?', abbreviated). Live audit
# (2026-07-20): 'kantor nya dmn' (where's the office), 'Harganya mulai dri brp' (from how
# much) were skipped as 'no_question'. dmn=dimana, brp/brapa=berapa, gmn=gimana.
_SHORTHAND_Q_RE = re.compile(
    r"\b(dmn|dmna|dimna|brp|brapa|brpa|brapaan|brpaan|gmn|gmna|kpn|jm\s*brp|"
    r"lokasi|kantor|alamat)\b|(mulai|dari|dr)\s*(dri|dari)?\s*brp",
    re.IGNORECASE)


def classify_comment(text: str) -> tuple[str, str]:
    """Return (action, reason). action ∈ {'reply','skip','hide'}.
    'reply' — a real question or interest, worth a public answer + possible DM.
    'hide'  — spam or abuse under our post.
    'skip'  — noise/praise/off-topic: leave it, don't spend a reply."""
    t = (text or "").strip()
    if not t:
        return "skip", "empty"
    if _SPAM_RE.search(t):
        return "hide", "spam"
    if _ABUSE_RE.search(t):
        return "hide", "abuse"
    if not _HAS_LETTER_RE.search(t):
        return "skip", "no_text"  # emoji / punctuation only
    if _TAGS_ONLY_RE.match(t):
        return "skip", "tags_only"  # just tagging friends, not talking to us
    if _NOISE_RE.match(t):
        return "skip", "praise_noise"
    if (is_answerable_question(t) or PRICE_QUESTION_RE.search(t)
            or PAYMENT_INTENT_RE.search(t) or BUYING_SIGNAL_RE.search(t)
            or _INTEREST_RE.search(t) or _SHORTHAND_Q_RE.search(t)):
        return "reply", "question_or_interest"
    return "skip", "no_question"  # a statement with no ask — don't force a reply


def is_warm(text: str) -> bool:
    """Warm enough to invite into DMs (real buying intent, not just a factual question)."""
    t = text or ""
    return bool(PAYMENT_INTENT_RE.search(t) or BUYING_SIGNAL_RE.search(t)
                or _INTEREST_RE.search(t))

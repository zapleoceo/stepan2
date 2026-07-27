"""Reply sanitization — strip LLM artifacts from outgoing text before delivery.

Zero-width chars, AI-style dashes and curly quotes, and fabricated Indonesian phone
numbers are removed. Lines containing the official IT STEP number are never stripped.
Ported from Stepan 1 `decision_parser._clean_reply()`."""
from __future__ import annotations

import re
import unicodedata

# Zero-width + word-joiner control chars that reasoning models inject silently
_ZW = dict.fromkeys(map(ord, "​‌‍﻿⁠"), None)

# Em/en dash (—, –, ―) → short dash with spaces — IM users don't type these
_DASH = re.compile(r"\s*[—–―]\s*")

# A literal backslash-n that survived JSON parsing: the model double-escaped its own reply
# ("…banget!\\n\\nTapi jujur ya Kak"), json.loads unescaped one level, and the second reached
# the lead as two visible characters. Live and current — 5 such messages went out on 27.07
# alone, all of them long money-answers, which is exactly where it looks worst.
# Converted to a real newline rather than deleted: the model put a paragraph break there and
# meant it, and the line filter below has to see it as a line break to do its own job.
_LITERAL_NEWLINE = re.compile(r"\\(?:r\\)?n")

# Other AI punctuation → human equivalents (curly quotes, ellipsis)
_HUMANIZE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'[""«»]'), '"'),
    (re.compile(r"[''']"), "'"),
    (re.compile(r"…"), "..."),  # … → three dots
    (re.compile(r" {2,}"), " "),     # collapse double spaces produced by above subs
]

# Official IT STEP WA/phone — lines containing this are NEVER stripped
_OFFICIAL_PHONE = re.compile(r"811[\s.\-]?1314[\s.\-]?400")

# IG contact-card line the LLM copies ("📱 Телефон · …")
_FAKE_PHONE_LINE = re.compile(r"\U0001f4f1\s*(?:телефон|telepon|phone)", re.I)

# Indonesian phone: +62… or 08x… with a long digit tail.
# Prices (Rp/jt/IDR) do NOT start with +62/08x → no false positives on prices.
_PHONE_PAT = re.compile(r"(?:\+62|\b08[1-9])[\d\s.\-]{6,}")

# Markdown artifacts reasoning models emit — IM users never type these, so strip the
# markers (keep the text). Single-`*` italic is left alone: too risky next to prices/×.
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_BULLET = re.compile(r"^(\s*)[-*]\s+", re.M)


def _strip_markdown(s: str) -> str:
    s = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _MD_HEADER.sub("", s)
    return _MD_BULLET.sub(r"\1• ", s)


def _has_fake_phone(line: str) -> bool:
    """True if line contains a phone-like token that is NOT the official IT STEP number."""
    return any(
        not _OFFICIAL_PHONE.search(m.group(0)) for m in _PHONE_PAT.finditer(line)
    )


# A URL ending a sentence collects the full stop: "…/view." — 39 such messages went out in
# 30 days. Instagram's autolinker takes the dot as part of the address, so the lead taps a
# link that 404s. The sentence reads the same without it; the link only works without it.
_URL_TRAILING_DOT = re.compile(r"(https?://[^\s]*[a-zA-Z0-9/=_-])\.(?=\s|$)")


def clean_reply(text: str) -> str:
    """Strip zero-width chars, AI punctuation, and fabricated Indonesian phone lines."""
    s = (text or "").translate(_ZW)
    s = _LITERAL_NEWLINE.sub("\n", s)
    s = _strip_markdown(s)
    s = _DASH.sub(" - ", s)
    s = _URL_TRAILING_DOT.sub(r"\1", s)
    for pat, repl in _HUMANIZE:
        s = pat.sub(repl, s)
    # Drop remaining C-category control chars (keep \n)
    s = "".join(ch for ch in s if ch == "\n" or unicodedata.category(ch)[0] != "C")
    lines = [
        ln for ln in s.split("\n")
        if not (_FAKE_PHONE_LINE.search(ln) and not _OFFICIAL_PHONE.search(ln))
        and not _has_fake_phone(ln)
    ]
    return "\n".join(lines).strip()

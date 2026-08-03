"""Reply sanitization — strip LLM artifacts from outgoing text before delivery.

Zero-width chars, AI-style dashes and curly quotes, and fabricated phone numbers are removed.
Lines containing the branch's own official number are never stripped. What counts as a phone
number depends on the country, so the shapes come from the branch's dialling code rather than
being hardcoded to Indonesia's — they were, and every other branch shipped invented local
numbers unchecked. Ported from Stepan 1 `decision_parser._clean_reply()`."""
from __future__ import annotations

import re
import unicodedata

from app.config import settings

from .dates import strip_date_annotations

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

# Our own date annotation, copied into the reply. dates.py marks every date in the knowledge
# base with the weekday it falls on and how far off it is — "1 September 2026 [hari Selasa, 36
# hari lagi]" — precisely so the model repeats arithmetic instead of doing it. It works, and
# the model duly repeats it: brackets and all, to the lead (sim, 27.07). Stripping it here is
# the same trade the annotation itself makes — the model cannot be relied on to leave a marker
# out, and it does not have to be, because the marker has already done its job by the time the
# text reaches this function.

# Other AI punctuation → human equivalents (curly quotes, ellipsis)
_HUMANIZE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'[""«»]'), '"'),
    (re.compile(r"[''']"), "'"),
    (re.compile(r"…"), "..."),  # … → three dots
    (re.compile(r" {2,}"), " "),     # collapse double spaces produced by above subs
]

def _official_phone_re() -> re.Pattern[str]:
    """Match the branch's own number in any spacing the model happens to write it.

    Hardcoding it rotted: the constant still held the retired 811 1314 400 when the number
    changed, so every line carrying the NEW official number looked fabricated and was deleted
    whole — taking the wa.me link with it (live thread 452, 31.07.2026: the lead got "вот
    ссылка на наш WhatsApp:" and nothing after it). Reading it from settings means changing
    the number in one place changes it here too."""
    digits = re.sub(r"\D", "", settings().official_phone_e164).removeprefix("62")
    groups = (digits[:3], digits[3:7], digits[7:])
    return re.compile(r"[\s.\-]?".join(re.escape(g) for g in groups if g))


# Official IT STEP WA/phone — lines containing this are NEVER stripped
_OFFICIAL_PHONE = _official_phone_re()

# IG contact-card line the LLM copies ("📱 Телефон · …")
_FAKE_PHONE_LINE = re.compile(r"\U0001f4f1\s*(?:телефон|telepon|phone)", re.I)

# A phone number, in the shapes the branch's own country writes them: the international form
# (+<dialling code>…) and the national trunk form, which carries no '+' at all and is therefore
# invisible to the international pattern. Indonesian mobiles start 08; elsewhere the safe
# generic is a trunk zero plus two digits.
#
# This was hardcoded to Indonesia until 2026-08-03, so a branch anywhere else shipped invented
# local numbers to its leads with nothing to stop them. Default "62" keeps branch 1 on exactly
# the pattern it has always run. Prices (Rp/jt/IDR) never start with +62/08x, so this cannot
# eat a figure.
_NATIONAL_TRUNK = {"62": r"08[1-9]"}
_DEFAULT_TRUNK = r"0\d{2}"


def _phone_pattern(country_code: str) -> re.Pattern[str]:
    cc = re.sub(r"\D", "", country_code) or "62"
    trunk = _NATIONAL_TRUNK.get(cc, _DEFAULT_TRUNK)
    return re.compile(rf"(?:\+{cc}|\b{trunk})[\d\s.\-]{{6,}}")


_PHONE_PATTERNS: dict[str, re.Pattern[str]] = {}


def _phone_re(country_code: str) -> re.Pattern[str]:
    """Compiled once per country code — clean_reply runs on every outgoing bubble."""
    if country_code not in _PHONE_PATTERNS:
        _PHONE_PATTERNS[country_code] = _phone_pattern(country_code)
    return _PHONE_PATTERNS[country_code]

# Markdown artifacts reasoning models emit — IM users never type these, so strip the
# markers (keep the text). Single-`*` italic is left alone: too risky next to prices/×.
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_BULLET = re.compile(r"^(\s*)[-*]\s+", re.M)


def _strip_markdown(s: str) -> str:
    s = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _MD_HEADER.sub("", s)
    return _MD_BULLET.sub(r"\1• ", s)


def _has_fake_phone(line: str, country_code: str) -> bool:
    """True if line contains a phone-like token that is NOT the branch's official number."""
    return any(
        not _OFFICIAL_PHONE.search(m.group(0)) for m in _phone_re(country_code).finditer(line)
    )


# A URL ending a sentence collects the full stop: "…/view." — 39 such messages went out in
# 30 days. Instagram's autolinker takes the dot as part of the address, so the lead taps a
# link that 404s. The sentence reads the same without it; the link only works without it.
_URL_TRAILING_DOT = re.compile(r"(https?://[^\s]*[a-zA-Z0-9/=_-])\.(?=\s|$)")


def clean_reply(text: str, country_code: str = "62") -> str:
    """Strip zero-width chars, AI punctuation, and fabricated phone lines.

    `country_code` is the branch's dialling code (BranchSettings.phone_country_code) — it
    decides which number shapes count as a phone at all."""
    s = (text or "").translate(_ZW)
    s = _LITERAL_NEWLINE.sub("\n", s)
    s = strip_date_annotations(s)
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
        and not _has_fake_phone(ln, country_code)
    ]
    return "\n".join(lines).strip()

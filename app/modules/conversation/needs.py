"""Customer-needs profile (Value Proposition Canvas) — parse, merge, render, gate.

The lead's discovered jobs/pains/gains accumulate across turns on `lead.needs` (JSON).
Each turn the model returns its current understanding; we union it with what's stored so
nothing is lost, feed it back into the next prompt, and gate presentation on it."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_MAX_PER_LIST = 6


@dataclass
class NeedsProfile:
    jobs: list[str] = field(default_factory=list)
    pains: list[str] = field(default_factory=list)
    gains: list[str] = field(default_factory=list)
    discovery_complete: bool = False

    def has_needs(self) -> bool:
        """A pain AND a gain captured — the emotional layer (cost of inaction) reached, not
        just a surface goal. A lone job/pain/gain is too shallow to present against: live
        chats showed the model gating on a single shallow item and jumping to a feature dump
        right after the lead stated a goal, skipping SPIN's implication/need-payoff beats."""
        return bool(self.pains and self.gains)

    def captured(self) -> bool:
        """Discovery has actually collected the emotional layer — the gate to stop warming up
        and present. Requires a PAIN and a GAIN, i.e. exactly has_needs().

        discovery_complete used to bypass the gain half (flag + any pain was enough). Two live
        failures killed that shortcut: the model sets the flag PREMATURELY with pains=[] (thread
        1081 — a pain-less 'complete' is not complete), and when it does catch a pain it flips
        the flag on that very turn and dumps the price with gains still empty — the 3-day audit
        (2026-07-15) found discovery breaking exactly where it starts working, answering
        'kurangnya modal dan ragu untuk memulai' with 'total Rp 1.882.955'. The flag is the
        model's own opinion; the pain+gain pair is evidence. _NEED_PAYOFF_NUDGE walks it from
        one to the other, and _DISCOVERY_TURN_CAP still releases a non-yielding lead."""
        return self.has_needs()

    def to_json(self) -> str:
        return json.dumps({
            "jobs": self.jobs, "pains": self.pains, "gains": self.gains,
            "discovery_complete": self.discovery_complete,
        }, ensure_ascii=False)


def parse_needs(raw: str | None) -> NeedsProfile:
    if not raw:
        return NeedsProfile()
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return NeedsProfile()
    if not isinstance(d, dict):
        return NeedsProfile()
    return NeedsProfile(
        jobs=_clean(d.get("jobs")), pains=_clean(d.get("pains")),
        gains=_clean(d.get("gains")), discovery_complete=bool(d.get("discovery_complete")),
    )


def merge_needs(
    stored: NeedsProfile, jobs: list[str], pains: list[str], gains: list[str],
    discovery_complete: bool,
) -> NeedsProfile:
    """Union the newly-discovered lists into the stored profile (order-preserving, capped)."""
    return NeedsProfile(
        jobs=_union(stored.jobs, jobs), pains=_union(stored.pains, pains),
        gains=_union(stored.gains, gains),
        discovery_complete=stored.discovery_complete or discovery_complete,
    )


def needs_summary(p: NeedsProfile) -> str:
    """A compact block injected into the prompt so the model keeps working the same need."""
    if not (p.jobs or p.pains or p.gains):
        return ""
    lines = ["KNOWN LEAD NEEDS (keep refining, present against these — don't re-ask what's here):"]
    if p.jobs:
        lines.append("- jobs (what they want to achieve): " + "; ".join(p.jobs))
    if p.pains:
        lines.append("- pains (fears/obstacles): " + "; ".join(p.pains))
    if p.gains:
        lines.append("- gains (desired outcomes): " + "; ".join(p.gains))
    return "\n".join(lines)


# Indonesian filler that doesn't carry a need's meaning — dropped before near-dup comparison
# so "pengen buat aplikasi", "membuat aplikasi", "bisa bikin aplikasi sendiri" collapse to the
# same content ({aplikasi}) instead of accumulating as four separate jobs (thread 1081).
_STOP = frozenset({
    "buat", "membuat", "bikin", "membikin", "pengen", "pengin", "ingin", "mau", "bisa", "dapat",
    "untuk", "dengan", "pakai", "memakai", "menggunakan", "sendiri", "aku", "saya", "kak", "kakak",
    "yang", "jadi", "menjadi", "sebuah", "agar", "supaya", "biar", "dan", "atau", "nanti", "kira",
    "punya", "adalah", "ini", "itu", "ke", "di", "dari", "sudah", "udah", "lebih", "juga", "the",
    "a", "an", "to", "of", "my", "i", "want", "be", "able", "with", "make", "build", "own",
    "bantu", "dibantu", "membantu", "bantuin", "tolong", "help",
})


def _content_tokens(s: str) -> frozenset[str]:
    toks = re.findall(r"[a-z0-9]+", s.lower())
    return frozenset(t for t in toks if len(t) >= 2 and t not in _STOP)


def _near(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two need phrases mean the same thing when their content-word sets are nested (one a
    subset of the other — the more specific supersedes) or overlap heavily (Jaccard >= 0.6)."""
    if not a or not b:
        return a == b
    if a <= b or b <= a:
        return True
    return len(a & b) / len(a | b) >= 0.6


def _dedup_near(items: list[str]) -> list[str]:
    """Collapse near-duplicate phrasings, keeping the MOST specific (most content words; ties
    → longer string) so a reworded restatement doesn't add a new entry (thread 1081)."""
    kept: list[str] = []
    sets: list[frozenset[str]] = []
    for raw in items:
        s = raw.strip()
        if not s:
            continue
        toks = _content_tokens(s)
        for i, ks in enumerate(sets):
            if _near(toks, ks):
                if len(toks) > len(ks) or (len(toks) == len(ks) and len(s) > len(kept[i])):
                    kept[i], sets[i] = s, toks  # upgrade to the more specific phrasing
                break
        else:
            kept.append(s)
            sets.append(toks)
    return kept[:_MAX_PER_LIST]


# A "pain" that is really just the lead's own QUESTION — the model files "ini ai ya?" (are you
# a bot?), "smm itu apa?" or "berbayar?" as a captured pain, which then satisfies the
# presentation gate and pollutes the needs cloud (3-day audit, 2026-07-15).
_QUESTION_START_RE = re.compile(
    r"^\s*(apa|apakah|gimana|bagaimana|berapa|kapan|kenapa|mengapa|bisa|boleh|ada)\b",
    re.IGNORECASE)
# Indonesian just as often puts the question word LAST — "smm itu apa", "harganya berapa",
# "mulainya kapan" — so an end-anchored check is needed too, not only a leading one.
_QUESTION_END_RE = re.compile(
    r"\b(apa|apakah|gimana|bagaimana|berapa|kapan|kenapa|mengapa)\s*\??\s*$", re.IGNORECASE)


def is_question(s: str) -> bool:
    t = (s or "").strip()
    return (t.endswith("?") or bool(_QUESTION_START_RE.match(t))
            or bool(_QUESTION_END_RE.search(t)))


def lead_grounded(items: list[str], lead_text: str) -> list[str]:
    """Keep only entries that share a content word with what the LEAD ACTUALLY WROTE.

    The model invents needs out of thin air — the ad creative's own copy ("upgrade skill",
    "ubah online time jadi peluang karier") filed as the lead's jobs though they never typed a
    word, or "serangan siber" read into a joke about a girlfriend. `lead_text` must exclude the
    ad prefill: a button click is not the lead speaking. Deliberately loose (ONE shared content
    word is enough) so honest rewording survives — "tidak paham coding" for "ga ngerti coding"
    still shares 'coding'."""
    lead_toks = _content_tokens(lead_text)
    if not lead_toks:
        return []  # lead said nothing of their own — nothing can be grounded in it
    return [s for s in items if _content_tokens(s) & lead_toks]


def _clean(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedup_near([str(item) for item in value])


def _union(base: list[str], extra: list[str]) -> list[str]:
    return _dedup_near(list(base) + list(extra))

"""LeadDossier — everything the seller knows about this person, carried across turns.

Supersedes the v2 `needs` JSON, which leaked in four ways the 2026-07-22 review pinned down:
it was written ONLY on a live reply (follow-ups and reactivation learned nothing), objections
were wholesale REPLACED each turn (forget to re-list one and it was gone forever), a
word-overlap filter deleted any pain the model phrased better than the lead did, and nothing
recorded what the bot had already SAID — so repetition had to be caught by diffing raw text.

The dossier fixes each: it is written on every workflow, objections accumulate with a status,
no grounding filter runs, and `spent` records what has been used so the model can simply be
told what not to repeat.

Stored on `lead.dossier`; a lead still carrying only the legacy `lead.needs` is converted on
read, so no thread loses context when v3 goes live."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .needs import NeedsProfile, dedup_phrases, parse_needs

_MAX_PER_LIST = 6
_MAX_OBJECTIONS = 8

ROLES = ("school", "student", "working", "jobseeking", "parent")
READINESS = ("exploring", "considering", "ready")
DECIDES_WITH = ("self", "parents", "family")
# How firmly the lead has said no. Indonesian rarely says it outright, so the three grades get
# three different reactions: soft ("pikir-pikir dulu") = one open question about the doubt,
# never an argument; vague ("makasih infonya") = accept, one delayed touch; blunt ("nggak
# usah") = stop, zero follow-ups. Treating all three alike is what got leads hammered.
REFUSALS = ("none", "soft", "vague", "blunt")
# The objection categories the model classifies into — kept for CRM/analytics and the
# dossier schema; the whole playbook loads in full as part of the stable prompt prefix.
OBJECTION_CATEGORIES = (
    "price", "time", "trust", "job_outcome", "self_study_free", "parent_approval",
)


@dataclass(frozen=True)
class Objection:
    """One objection and whether it is still live. `handled_by` is how it was answered, so a
    later turn can reference the answer instead of repeating it.

    `category` picks which section of the objection playbook (a knowledge_doc, sections
    named after these categories) gets loaded into context — never the whole playbook, only
    the one that matches what this lead actually raised. Empty when the model doesn't
    recognise a category; nothing extra loads for those, same as an unhandled fact."""

    text: str
    status: str = "open"  # open | handled
    handled_by: str = ""
    category: str = ""  # price|time|trust|job_outcome|self_study_free|parent_approval|other

    def as_dict(self) -> dict[str, str]:
        return {"text": self.text, "status": self.status, "handled_by": self.handled_by,
                "category": self.category}


@dataclass
class LeadDossier:
    """The seller's working memory of one lead."""

    # who
    role: str = ""
    # why
    job_to_be_done: str = ""
    pains: list[str] = field(default_factory=list)
    desired_state: list[str] = field(default_factory=list)
    # decision
    decides_with: str = ""
    readiness: str = ""
    # money
    prices_quoted: list[str] = field(default_factory=list)
    payment_preference: str = ""
    budget_signal: str = ""
    # objections, accumulating with status
    objections: list[Objection] = field(default_factory=list)
    # spent — what has already been used, so it is never served twice
    products_named: list[str] = field(default_factory=list)
    cases_used: list[str] = field(default_factory=list)
    arguments_used: list[str] = field(default_factory=list)
    refusal: str = "none"

    def open_objections(self) -> list[str]:
        return [o.text for o in self.objections if o.status == "open"]

    def open_objection_categories(self) -> frozenset[str]:
        """The categories of still-open objections — analytics/CRM surface."""
        return frozenset(o.category for o in self.objections
                         if o.status == "open" and o.category)

    def has_discovery(self) -> bool:
        """A pain AND a desired state — the emotional layer, not just a surface goal. Same bar
        v2 gated presentation on, kept because live chats showed a single shallow item let the
        model jump straight to a feature dump."""
        return bool(self.pains and self.desired_state)

    def to_json(self) -> str:
        return json.dumps({
            "role": self.role,
            "job_to_be_done": self.job_to_be_done,
            "pains": self.pains,
            "desired_state": self.desired_state,
            "decides_with": self.decides_with,
            "readiness": self.readiness,
            "prices_quoted": self.prices_quoted,
            "payment_preference": self.payment_preference,
            "budget_signal": self.budget_signal,
            "objections": [o.as_dict() for o in self.objections],
            "products_named": self.products_named,
            "cases_used": self.cases_used,
            "arguments_used": self.arguments_used,
            "refusal": self.refusal,
        }, ensure_ascii=False)


def parse_dossier(raw: str | None, legacy_needs: str | None = None) -> LeadDossier:
    """The stored dossier, or one reconstructed from the legacy needs JSON when absent.

    Both may be present during the v2→v3 window; the dossier wins, since it is the one being
    kept current."""
    parsed = _from_json(raw)
    if parsed is not None:
        return parsed
    return from_needs(parse_needs(legacy_needs)) if legacy_needs else LeadDossier()


def from_needs(needs: NeedsProfile) -> LeadDossier:
    """Legacy NeedsProfile → dossier. jobs[0] becomes the job-to-be-done and any further jobs
    join the desired state; every stored objection was by definition still open."""
    jobs = list(needs.jobs)
    return LeadDossier(
        job_to_be_done=jobs[0] if jobs else "",
        pains=list(needs.pains),
        desired_state=dedup_phrases(list(needs.gains) + jobs[1:]),
        objections=[Objection(text=t) for t in needs.objections][:_MAX_OBJECTIONS],
    )


def merge_dossier(stored: LeadDossier, delta: LeadDossier) -> LeadDossier:
    """Fold this turn's findings into what is already known.

    Scalars: a non-empty value overwrites (the model is restating its current read). Phrase
    lists: union, deduped and capped — nothing learned is ever dropped. Objections: matched by
    meaning and upgraded open→handled, never removed, because forgetting to re-list one was
    exactly how v2 lost them. Refusal: the latest reading wins, so a lead who re-engages after
    a hard no is not silenced forever."""
    return LeadDossier(
        role=_pick(delta.role, stored.role, ROLES),
        job_to_be_done=delta.job_to_be_done.strip() or stored.job_to_be_done,
        pains=_union(stored.pains, delta.pains),
        desired_state=_union(stored.desired_state, delta.desired_state),
        decides_with=_pick(delta.decides_with, stored.decides_with, DECIDES_WITH),
        readiness=_pick(delta.readiness, stored.readiness, READINESS),
        prices_quoted=_union(stored.prices_quoted, delta.prices_quoted),
        payment_preference=delta.payment_preference.strip() or stored.payment_preference,
        budget_signal=delta.budget_signal.strip() or stored.budget_signal,
        objections=_merge_objections(stored.objections, delta.objections),
        products_named=_union(stored.products_named, delta.products_named),
        cases_used=_union(stored.cases_used, delta.cases_used),
        arguments_used=_union(stored.arguments_used, delta.arguments_used),
        refusal=_pick(delta.refusal, stored.refusal, REFUSALS) or "none",
    )


# ── internal helpers ──────────────────────────────────────────────────────────

def _pick(new: str, old: str, allowed: tuple[str, ...]) -> str:
    """The new value when it is a recognised option, else whatever was already stored — a model
    typo must not erase a known fact."""
    candidate = (new or "").strip().lower()
    return candidate if candidate in allowed else old


def _union(base: list[str], extra: list[str]) -> list[str]:
    return dedup_phrases(list(base) + list(extra))[:_MAX_PER_LIST]


def _merge_objections(stored: list[Objection], delta: list[Objection]) -> list[Objection]:
    """Accumulate by meaning: a delta entry matching a stored one updates its status (only ever
    open→handled), anything new is appended."""
    out = list(stored)
    for incoming in delta:
        text = incoming.text.strip()
        if not text:
            continue
        index = _match_objection(out, text)
        if index is None:
            out.append(Objection(text, _status(incoming.status), incoming.handled_by.strip(),
                                 _category(incoming.category)))
        else:
            existing = out[index]
            out[index] = Objection(
                existing.text,
                "handled" if incoming.status == "handled" else existing.status,
                incoming.handled_by.strip() or existing.handled_by,
                _category(incoming.category) or existing.category)
    return out[:_MAX_OBJECTIONS]


def _match_objection(items: list[Objection], text: str) -> int | None:
    """Index of the entry meaning the same thing, using the same near-match the phrase lists
    use: two texts collapse iff dedup keeps only one of them."""
    for i, existing in enumerate(items):
        if len(dedup_phrases([existing.text, text])) == 1:
            return i
    return None


def _status(value: str) -> str:
    return "handled" if (value or "").strip().lower() == "handled" else "open"


def _category(value: str) -> str:
    candidate = (value or "").strip().lower()
    return candidate if candidate in OBJECTION_CATEGORIES else ""


def _from_json(raw: str | None) -> LeadDossier | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    return LeadDossier(
        role=_pick(str(d.get("role") or ""), "", ROLES),
        job_to_be_done=str(d.get("job_to_be_done") or "").strip(),
        pains=_strings(d.get("pains")),
        desired_state=_strings(d.get("desired_state")),
        decides_with=_pick(str(d.get("decides_with") or ""), "", DECIDES_WITH),
        readiness=_pick(str(d.get("readiness") or ""), "", READINESS),
        prices_quoted=_strings(d.get("prices_quoted")),
        payment_preference=str(d.get("payment_preference") or "").strip(),
        budget_signal=str(d.get("budget_signal") or "").strip(),
        objections=_objections(d.get("objections")),
        products_named=_strings(d.get("products_named")),
        cases_used=_strings(d.get("cases_used")),
        arguments_used=_strings(d.get("arguments_used")),
        refusal=_pick(str(d.get("refusal") or ""), "none", REFUSALS),
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return dedup_phrases([str(v) for v in value])[:_MAX_PER_LIST]


def _objections(value: object) -> list[Objection]:
    if not isinstance(value, list):
        return []
    out: list[Objection] = []
    for item in value:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            out.append(Objection(
                str(item["text"]).strip(), _status(str(item.get("status") or "")),
                str(item.get("handled_by") or "").strip(),
                _category(str(item.get("category") or ""))))
        elif isinstance(item, str) and item.strip():
            out.append(Objection(item.strip()))
    return out[:_MAX_OBJECTIONS]

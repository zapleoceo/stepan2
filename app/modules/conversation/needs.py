"""Customer-needs profile (Value Proposition Canvas) — parse, merge, render, gate.

The lead's discovered jobs/pains/gains accumulate across turns on `lead.needs` (JSON).
Each turn the model returns its current understanding; we union it with what's stored so
nothing is lost, feed it back into the next prompt, and gate presentation on it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

_MAX_PER_LIST = 6


@dataclass
class NeedsProfile:
    jobs: list[str] = field(default_factory=list)
    pains: list[str] = field(default_factory=list)
    gains: list[str] = field(default_factory=list)
    discovery_complete: bool = False

    def has_needs(self) -> bool:
        """At least one pain or gain captured — enough to present against a real need."""
        return bool(self.pains or self.gains)

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


def _clean(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= _MAX_PER_LIST:
            break
    return out


def _union(base: list[str], extra: list[str]) -> list[str]:
    seen = {s.lower(): s for s in base}
    out = list(base)
    for s in extra:
        s = s.strip()
        if s and s.lower() not in seen:
            seen[s.lower()] = s
            out.append(s)
        if len(out) >= _MAX_PER_LIST:
            break
    return out

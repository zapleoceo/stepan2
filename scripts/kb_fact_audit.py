"""One-off / repeatable: audit the KB for facts that are intentionally duplicated across
multiple docs as ready-to-say scripts (needed verbatim in places, for RAG-retrieval
reliability — see docs/kb-fact-sync.md) — and flag any copy that has drifted from the
canonical wording after an edit only touched one location.

Run in the container:  python -m scripts.kb_fact_audit [--branch N]

Add a new TrackedFact to _FACTS whenever a fact starts living in more than one doc, so the
next time it changes, drift gets caught instead of discovered live (as happened 2026-07-07:
the Stepan-origin story was updated in 4 places, but 2 more copies — in playbook_ready and
playbook_social — were missed until a lead's direct question surfaced the stale version).
"""
from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass

from sqlalchemy import text

from app.adapters.db.session import session_scope


@dataclass(frozen=True)
class TrackedFact:
    name: str
    # Any doc/product whose content matches this pattern is a candidate location for the fact.
    trigger: re.Pattern[str]
    # Every candidate location must ALSO match this pattern within `window` chars of the
    # trigger, or it's flagged as stale/drifted — edited in one place, not propagated here.
    must_contain: re.Pattern[str]
    window: int = 400


_FACTS: list[TrackedFact] = [
    TrackedFact(
        name="stepan_origin_story",
        trigger=re.compile(
            r"\bStepan\b(?:(?!\.\s*\n\n).){0,300}\bDirector\b"
            r"|\bDirector\b(?:(?!\.\s*\n\n).){0,300}\bStepan\b",
            re.DOTALL,
        ),
        must_contain=re.compile(r"alumnus|alumni", re.IGNORECASE),
    ),
]


def find_drifted_locations(
    fact: TrackedFact, rows: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """rows: (kind, slug, content). Returns (kind, slug, snippet) for every location whose
    trigger match lacks the canonical must_contain marker nearby."""
    flags: list[tuple[str, str, str]] = []
    for kind, slug, content in rows:
        for m in fact.trigger.finditer(content):
            start = max(0, m.start() - fact.window)
            end = min(len(content), m.end() + fact.window)
            if not fact.must_contain.search(content[start:end]):
                snippet = content[max(0, m.start() - 40):m.end() + 40]
                flags.append((kind, slug, snippet))
    return flags


async def _rows(session, branch: int) -> list[tuple[str, str, str]]:
    docs = (await session.execute(text(
        "SELECT 'doc', slug, content FROM knowledge_doc WHERE branch_id=:b"), {"b": branch}
    )).all()
    prods = (await session.execute(text(
        "SELECT 'product', slug, content FROM product WHERE branch_id=:b"), {"b": branch}
    )).all()
    return [tuple(r) for r in (*docs, *prods)]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", type=int, default=1)
    args = ap.parse_args()

    async with session_scope() as session:
        rows = await _rows(session, args.branch)

    total = 0
    for fact in _FACTS:
        print(f"=== {fact.name} ===")
        flags = find_drifted_locations(fact, rows)
        hits = sum(1 for _k, _s, _c in rows for _ in fact.trigger.finditer(_c))
        if hits == 0:
            print("  (no occurrences found)")
        for kind, slug, snippet in flags:
            total += 1
            print(f"  DRIFT  {kind}:{slug}  …{snippet!r}…")
        if hits and not flags:
            print(f"  OK — {hits} occurrence(s), all consistent")

    if total:
        print(f"\n{total} drifted location(s) found.")
    else:
        print("\nAll tracked facts are consistent.")


if __name__ == "__main__":
    asyncio.run(main())

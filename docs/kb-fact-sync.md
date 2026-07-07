# Fact duplication in the KB — why, and how it's kept in sync

Some facts (like "who built Stepan") need to appear in more than one knowledge-base
document — not because we didn't notice the duplication, but because the RAG retrieval
system pulls documents by relevance to the current turn, not all-related-docs-at-once. A
playbook that says "see the STORIES card" for a fact it needs to state verbatim risks the
STORIES card simply not being in context that turn (we've hit this exact bug before — a
"(see persona)" pointer that referenced a document the model never actually receives).

So the rule is:
- **A pointer-only reference** ("use the STORIES card for X") is fine when the doc never
  needs to restate X itself — it's just reminding the model the fact exists somewhere.
- **A verbatim copy** is justified when a doc needs the fact usable in-line, in that exact
  moment, regardless of what else got retrieved that turn (e.g. a ready-to-say reveal line).

The failure mode is drift: editing the fact in the canonical doc without updating every
verbatim copy. This happened 2026-07-07 — the Stepan-origin story (built by a course
alumnus, not the Branch Director) was updated in 4 places, but 2 more copies (in
`playbook_ready` and `playbook_social`) still said the old version until a lead's direct
question surfaced it live.

## Tracked fact: Stepan's origin story

**Canonical source:** `stories` doc, "#1 hero proof" section.

**Current fact:** Stepan (this AI assistant) was built by a Vibe Coding alumnus who had no
coding background before the course; he turned it into a startup now used by several
companies, including IT STEP Academy itself. (The Branch Director's own real projects —
`dima.veranda.my`, `zapleo.com`, `wallishcompany.com` — are a SEPARATE, still-true claim:
he built those himself, without a CS background, the same AI-assisted way.)

**Verbatim copies that must be updated together if this fact changes:**
- `stories` — hero proof list + the honest reveal line (canonical)
- `playbook_qualify` — the "I'm an AI" reveal script (POSITIONING section)
- `faq` — "Alumni projects / contoh hasil" section, and the Open House paragraph
- `product:open_house` — who demonstrates what at the event

**Pointer-only references (no restatement, safe as-is):**
- `playbook_ready`, `playbook_social` — "use the STORIES card for the real proofs"

## Keeping it consistent

Run the audit script after editing any tracked fact:

```
python -m scripts.kb_fact_audit --branch 1
```

It flags any location where the trigger co-occurs (e.g. "Stepan" near "Director") without
the canonical marker phrase ("alumnus"/"alumni") nearby — catching exactly the drift above.
Add a new `TrackedFact` in `scripts/kb_fact_audit.py` whenever a new fact starts living in
more than one document.

Remember: after any KB content edit, run `reindex_branch` (or the Coach's reindex button) —
RAG chunks are stale until reindexed, so a fixed doc can still surface the old wording for a
while otherwise (also hit live during this same fix).

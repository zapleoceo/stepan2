# Fact duplication in the KB — why, and how it's kept in sync

Some facts (like "who built Stepan") appear in more than one place — a facts doc, a product
card, and the reply prompt itself. Since the 2026-07-20 restructure the whole facts-only KB
is loaded into every prompt (no RAG, no retrieval — see
[knowledge-base.md](knowledge-base.md)), so a fact stated once is *always* in context and a
pointer-only reference is now always safe. Duplication that remains is deliberate: a fact
that must be usable **in-line** (a ready-to-say reveal line) is copied where it's needed, and
the former playbook tactics — including their reveal scripts — now live in `prompt.py`, not in
KB docs, which puts copies of a shared fact on both sides of the KB↔prompt boundary.

So the rule is:
- **A pointer-only reference** ("use the STORIES section for X") is fine when the doc never
  needs to restate X itself — it just reminds the model the fact exists somewhere in context.
- **A verbatim copy** is justified when a spot needs the fact usable in-line, in that exact
  moment (e.g. a ready-to-say reveal line in the prompt).

The failure mode is drift: editing the fact in the canonical doc without updating every
verbatim copy. This happened 2026-07-07 — the Stepan-origin story (built by a course
alumnus, not the Branch Director) was updated in several places while a couple of copies (in
the old `playbook_ready`/`playbook_social` docs, since absorbed into the prompt) still said
the old version until a lead's direct question surfaced it live.

## Tracked fact: Stepan's origin story

**Canonical source:** `facts_market`, the success-cases / hero-proof section.

**Current fact:** Stepan (this AI assistant) was built by a Vibe Coding alumnus who had no
coding background before the course; he turned it into a startup now used by several
companies, including IT STEP Academy itself. (The Branch Director's own real projects —
`dima.veranda.my`, `zapleo.com`, `wallishcompany.com` — are a SEPARATE, still-true claim:
he built those himself, without a CS background, the same AI-assisted way.)

**Verbatim copies that must be updated together if this fact changes** (after the restructure
the tactic docs are gone — the reveal scripts live in the prompt now):
- `facts_market` — hero-proof list + the honest reveal line (canonical)
- `prompt.py` (`_DECISION_CONTRACT`) — the "I'm an AI" reveal script (formerly the
  `playbook_qualify` POSITIONING section)
- `product:open_house` — who demonstrates what at the event

**Pointer-only references (no restatement, safe as-is):** none needed — the whole facts KB is
in context every turn, so any spot can just lean on the `facts_market` proof without copying.

## Keeping it consistent

Run the audit script after editing any tracked fact:

```
python -m scripts.kb_fact_audit --branch 1
```

It flags any location where the trigger co-occurs (e.g. "Stepan" near "Director") without
the canonical marker phrase ("alumnus"/"alumni") nearby — catching exactly the drift above.
Add a new `TrackedFact` in `scripts/kb_fact_audit.py` whenever a new fact starts living in
more than one document.

A KB content edit is live on the very next reply — the whole facts KB is loaded into every
prompt, so there is nothing to reindex and no stale-chunk window to wait out.

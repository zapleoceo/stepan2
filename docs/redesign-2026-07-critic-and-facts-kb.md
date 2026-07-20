# Reply-quality redesign — 2026-07-20

A fundamental change to how Stepan produces a reply, after a 6-slice audit found the pipeline
defined quality *negatively* (a growing pile of regex patches, each closing one past incident)
with no positive check that a reply is actually good, no memory of objections, a knowledge base
that duplicated every fact across ~40 docs (and had already drifted — Zoom vs Teams, a stale
event date), and RAG whose retrieval-miss was itself a source of fabrication.

## The four changes

### 1. Critic-gate (the keystone) — `conversation/critic.py`, `reply.apply_critic`
A strong-model pass judges **every** reply against a positive rubric before it can be sent:

- **grounded** — every concrete fact is present in the KB context
- **responsive** — it addresses what the lead actually said
- **sales_move** — it advances the sale by one sound step
- **objection** — a live objection is handled, not talked over
- **register** — right language, warm `Kak`, one question, no premature contact ask

On failure it regenerates once with the critic's feedback, re-judges, and if it still can't clear
the bar it **fails CLOSED** — hands the lead to a human (also on a broker error, the opposite of
the old `verify_grounding`, which failed open). It runs after every deterministic safety net, so
it has the last word and nothing downstream can resurrect a rejected reply.

Setting `critic_gate` (branch-scoped): `off` (default — sims/tests see the raw draft), `shadow`
(runs and logs the verdict but never alters the reply — use to measure the reject rate before
enabling), `on` (blocks + regens + hands off). The **live sales branch opts in**; everything
else stays off.

### 2. Objection-state — `open_objections`
`Decision` and `NeedsProfile` gained `open_objections` (the objections the lead has raised and
not yet accepted a reframe for). Unlike jobs/pains/gains it **replaces** each turn (the open set
shrinks as objections are handled). It's injected into the prompt ("handle before any pitch") and
passed to the critic — closing the structural cause of hammering the audit found (68% of
objections were answered "мимо").

### 3. Facts-only KB, no RAG — `knowledge/service.py`
The KB was restructured to **facts only**. The tactic "playbook" docs moved into the reply
prompt, so the whole fact surface now fits one context window and is sent **every turn**:

- `persona_core` — identity/voice only
- `facts_policy` — payment, discounts, certificates, referral, student rules + the NEVER-list
- `facts_market` — institution facts, competitor contrasts, platform (**Teams**), income ranges,
  success cases
- the **full focus card** of the product in play
- a one-line **QUICK FACTS** summary of every other product (the catalog)

No retrieval, no embeddings, no reindex. Removed: `rag.py`, `reindex.py`, `chunking.py`, the
`knowledge_chunk` table (migration `dropkc20260720`), the reindex cron/endpoint/button,
`rag_top_k`, and the coach's chunk retrieval (it now reads the whole KB). This also removes RAG's
failure mode — a retrieval miss letting the model invent a fact the right card would have
grounded.

Each product card carries a single `QUICK FACTS:` line (pipe-separated headline facts); the
catalog shows only that line for non-focus products, the full card when a product is in focus.

### 4. Prompt rewrite — `conversation/prompt.py`
`_DECISION_CONTRACT` absorbs the former playbook docs' methodology (SPIN discovery, price
psychology, objection→advantage reframes, closing, contact capture), fixes the
`discovery_complete` triple-contradiction (now **pain AND gain**, matching the code gate), and
strips every `thread NNNN` incident reference (the model can't read them). Result: ~21k chars,
smaller than the old ~35k despite carrying the tactics, and no longer duplicated in the KB.

## Reply pipeline order (live reply)
context load → language → routing (fast/smart) → situational nudge → model call → parse →
need-payoff regen → dedup regen → **guard_decision** (fabrication/policy, deterministic +
is_risky LLM verify) → post-guard dedup/clarify → premature-contact → promised-handoff →
answer-don't-escalate → phone gate → **apply_critic** (positive quality gate, fail-closed) →
record needs+objections → enqueue.

## Applying the KB to a branch (prod)
The restructured content is applied to prod branch 1 via SQL (dollar-quoted, revision-tracked so
it's reversible through the KB history):
- **phase 1** — update `persona_core` + the 15 product cards, insert `facts_policy` +
  `facts_market` (legacy docs still present, harmless — they're in the always-load list as a
  migration fallback).
- **phase 3** — after verifying, delete the 24 consolidated/tactic docs, leaving only
  `persona_core`, `facts_policy`, `facts_market`.
Then enable the critic on the branch: `critic_gate` = `shadow` to measure, then `on`.

## Known follow-ups
- The seed JSON (`knowledge/seeds/`) and `canonical_docs` still describe the pre-restructure KB;
  a fresh branch degrades gracefully (legacy `payment_policy`/`policy_prohibitions` load) but
  should be regenerated from the same content before any new branch is created.
- `open_objections` grounding reuses `lead_grounded`; if objection recall proves too loose/tight,
  tune there.
- Two business rules were resolved conservatively where the old KB contradicted itself and should
  be confirmed with the owner: the **student discount is under-18 only** (mahasiswa 18+ excluded),
  and **discounts don't stack** (referral included).

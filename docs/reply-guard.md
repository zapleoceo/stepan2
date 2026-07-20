# Reply-guard — draft-quality verification layer

The verification layer between generation and send. The prompt only *asks* the model to
stay grounded and to write well; the guard *checks* the draft and refuses to send it when it
violates a rule. It started (chat 1736) as an anti-fabrication check against the exact KB
context the model saw, and has since grown a second tier of **conversation-quality** checks
that need no KB context at all (see below).

The guard blocks known-bad shapes (fabrication); by construction it cannot tell whether a
clean, non-fabricated reply actually *sells*. That is the job of the newer **critic-gate**
(`conversation/critic.py`, `reply.apply_critic`) — a positive-rubric `chat:smart` check
(grounded / responsive / sales-move / objection-handled / register) that runs on EVERY reply
as the LAST pipeline step and fails CLOSED to a human. When the critic is `on` for a branch,
the guard's LLM-verify tier below is skipped as redundant (its `grounded` dimension is a
stricter, fail-closed version of the same check); the deterministic guard tiers always run.
See [reply-pipeline.md](reply-pipeline.md).

Motivated by chat 1736, where the bot invented a personalised lab URL
(`lab.itstep.id/...?access=HANDAYANI2024`), "free 3-day lab access", a Cisco cert track,
and freelance rates — none in the KB — and a real lead wasted a night acting on them.

## Pipeline (`app/modules/conversation/guard.py`, shared helper `guard_decision` in
`app/modules/conversation/reply.py`)

1. **Deterministic (free, always on):** any URL in the draft not present verbatim in the KB
   context is a fabrication → violation. Bare `itstep.id` is allowed; any path/query link
   must be grounded. This alone blocks the 1736 fake link.
2. **Selective LLM verify (`chat:fast`, `kind='guard'`):** only when the reply looks risky
   (offer/resource/access/link keywords, OR a concrete price figure — `Rp 297.000`, `13 juta`,
   `harganya` — even with no diskon/promo/gratis word) a cheap model lists claims unsupported
   by the KB context. Not run on ordinary replies → bounded cost.
3. **Regenerate once:** on any violation, re-ask the model with the fabrications named and a
   hard "don't invent links/labs/trials/rates/dates" instruction (forced `chat:smart`).
4. **Safe hand-off:** if a link is still ungrounded after regeneration, replace the reply
   with a defer-to-team message and set `needs_manager=True`. The invented fact never ships.

`guard_decision` runs from BOTH live replies (`ReplyService.decide`) and follow-up nudges
(`FollowupService._queue_followup`) — until 2026-07-05 only the live-reply path called the
guard, so a proactive nudge ("btw kami juga punya X, cuma Rp 297.000") could fabricate
freely. Chat 452 shipped exactly that: a Python Skill Booster quoted at Rp 297.000 against a
real price of Rp 500.000/600.000, caught only by manual review — the followup path skipped
the guard entirely, and even on the live-reply path `is_risky()` had no price keyword, so a
bare price claim slipped past the LLM-verify tier. Both gaps are closed.

Defense-in-depth: `persona_core` also carries a hard "NEVER FABRICATE" rule (no invented
links/labs/free access/promos/rates/dates/certs/stats; unknown → confirm with the team).

## Conversation-quality tier (deterministic, no KB context needed)

Live business review (Daniel/Dima, 2026-07-07) surfaced recurring style failures that don't
need the KB to detect. These run in `_deterministic_issues` alongside the URL/delivery checks
and go through the same regenerate-once flow:

- **`multiple_questions`** — 2+ `?` in one turn (across all `|||` bubbles) leaves one question
  unanswered (threads 1729/1793). Quoted `«…»` example scripts are stripped before counting.
- **`impossible_capability_offers`** — offering a voice note / call / video (thread 1330);
  Stepan is text-only Instagram DM. (A "the team will call you" hand-off is NOT this — only
  Stepan offering to do it himself.)
- **`wrong_channel_claims`** — telling an Instagram-DM lead to "go DM on Instagram" (thread
  2092); this conversation already IS Instagram.
- **`false_delivery_claims`** — claiming a file/screenshot was ALREADY sent, or delivered via
  WhatsApp (threads 1408/1721); Stepan can't attach files and has no WhatsApp channel.

**Graceful degrade for doubled questions:** unlike a fabrication, a still-doubled question
after the regen is a style slip, not a risk. When the ONLY remaining issue is
`multiple_questions`, `truncate_to_one_question` keeps the reply through the first real `?`
and drops the rest, instead of a full SAFE_FALLBACK hand-off (threads 2159/2160: a plain
"tell me more about the course" was needlessly handed to a manager because the regen also
doubled up). Every other violation type still hands off — there's no safe way to trim a
fabricated fact.

## Config

Per-branch setting `reply_guard`:
- `full` (default) — deterministic + LLM verify on risky replies.
- `urls` — deterministic URL check only (free).
- `off` — disabled.

Sim runs (`workflow='sim'`) pass through the guard unchanged, so the `sim_say` battery is
the regression gate: replay 1736-style bait and assert no URL / hand-off instead of a
fabrication.

## Verified (2026-07-05)

Sim replay of the 1736 scenario ("kirim akses lab virtual", "akses gratis / link lab"):
Stepan refused the invented lab, redirected to real offerings (Open House, Skill Booster),
deferred to the team, and handed off — zero fabricated URLs. `broker_log kind='guard'`
confirms the verify tier fired.

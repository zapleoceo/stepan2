# Reply-guard — anti-fabrication framework

The verification layer between generation and send. RAG + prompt only *ask* the model to
stay grounded; the guard *checks* the draft against the exact KB context the model saw and
refuses to send fabrications (invented links, free-lab access, made-up rates/certs/dates).

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

# Reply engine

One lead-turn, end to end. The model sells its own way; the code guards the money.

## Modules

| Module | Owns |
|---|---|
| `signals.py` | What an inbound message IS: ad prefill, auto-responder, a real question, a polite no, an explicit postponement. Used by ingest, comment triage and the digest — never decides what to say. |
| `dossier.py` | `LeadDossier` — the seller's working memory of one lead: who they are, why they came, who decides, money signals, objections with a status, and what has already been used on them. |
| `free_mode.py` | Prompt assembly: the byte-stable cached prefix (full KB + goal contract + schema), the small variable block (dossier, hints, now), the dialog. |
| `decision.py` | Parsing the model's JSON (tool-envelope tolerant), one generation with a single escalation, and the adapter to the `Decision` shape delivery consumes. |
| `routing.py` | Which model tier a turn runs on — read off the dossier and the turn index, never off the message text. Decisive turns ride `chat:sales`. |
| `money_gate.py` | The only deterministic check that blocks a send: a price, link, income claim or invented service absent from the knowledge base. Fails CLOSED. |
| `opener.py` | First-contact classification; silent/junk entries ship a zero-LLM template, typed ones go to the full pipeline. |
| `reply.py` | `ReplyService` — one turn: load the dossier, pick the tier, generate once (chat:smart fallback), money-gate, record what was learned. |
| `delivery.py` | `ReplyDelivery` — everything between a decision and the lead seeing it: bubbles, outbox, stage events, lead sync, hand-off alerts. Knows nothing about how the reply was produced. |
| `followup.py` / `reactivation.py` | Nudges to a lead who went quiet, and to one long dormant. Same prompt builder and prefix; both read AND write the dossier. |

## Call budget

| Turn | Calls |
|---|---|
| Routine | 1 |
| Unparseable / chain down | 2 — one retry on the fallback chain |
| Ceiling | 3 — generation + fallback retry or one money rewrite, never a chain |

## Invariants

- **The prefix is byte-stable.** messages[0] is identical across turns and leads — it is the broker's prompt-cache anchor. No conditional insertions.
- **Only money fails closed.** A figure/link/service not in the KB never ships: one rewrite, then the hold-line + escalation. Everything else is the model's call.
- **No stub, ever.** There is no canned fallback and no clarify menu; degrade to the cheaper chain, never to silence.
- **Escalate only on purpose.** A human is pulled in when the lead asks for one, complains, raises a legal issue, has a payment problem, or a money figure stays ungrounded after one rewrite.
- **State accumulates.** Objections are marked handled, never deleted. `spent` records what was used so nothing is served twice — repetition is prevented by telling the model, not by diffing its output.
- **Routing never reads message text.** An unseen phrasing cannot downgrade a decisive turn.

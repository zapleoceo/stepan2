# Reply engine

One lead-turn, end to end.

## Modules

| Module | Owns |
|---|---|
| `signals.py` | What an inbound message IS: ad prefill, auto-responder, a real question, a polite no, an explicit postponement. Used by ingest, comment triage and the digest — never decides what to say. |
| `dossier.py` | `LeadDossier` — the seller's working memory of one lead: who they are, why they came, who decides, money signals, objections with a status, and what has already been used on them. |
| `contract.py` | The instruction block and the enumerated set of moves. Prompt assembly lives here. |
| `decision.py` | Parsing the model's JSON, one generation with a single escalation, and the adapter to the `Decision` shape delivery consumes. |
| `routing.py` | Which model tier a turn runs on — read off the dossier and the turn index, never off the message text. |
| `money_gate.py` | The only deterministic check that blocks a send: a price, link or income claim absent from the knowledge base. Fails CLOSED. |
| `critic.py` | Does the reply SELL — does it answer, move, sound human. Fails OPEN. |
| `reply.py` | `ReplyService` — one turn: load the dossier, pick the tier, generate once, vet, record what was learned. |
| `delivery.py` | `ReplyDelivery` — everything between a decision and the lead seeing it: bubbles, outbox, stage events, lead sync, hand-off alerts. Knows nothing about how the reply was produced. |
| `followup.py` / `reactivation.py` | Nudges to a lead who went quiet, and to one long dormant. Both read AND write the dossier. |

## Call budget

| Turn | Calls |
|---|---|
| Routine | 1 |
| Decisive (opener, objection, money, closing) | 2 — generation + critic |
| Ceiling | 3 — one rewrite, never two |

The money gate and the critic never both spend a rewrite.

## Invariants

- **Answer first.** If the lead asked something, the first sentence answers it. This outranks every other instruction.
- **No stub, ever.** There is no canned fallback and no clarify menu. A rewrite is never judged a second time — a second rejection is what produced silence.
- **Escalate only on purpose.** A human is pulled in when the lead asks for one, complains, raises a legal issue, has a payment problem, or a money figure stays ungrounded after one rewrite. Not knowing something is not a reason.
- **State accumulates.** Objections are marked handled, never deleted. `spent` records what was used so nothing is served twice — repetition is prevented by telling the model, not by diffing its output.
- **Routing never reads message text.** An unseen phrasing cannot downgrade a decisive turn.

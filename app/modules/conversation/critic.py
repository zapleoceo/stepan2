"""Critic-gate — the positive quality check every outbound reply must pass.

The guard layer (guard.py) blocks KNOWN-BAD shapes (fabrication) by regex; by construction
it cannot tell whether a well-formed, non-fabricated reply actually SELLS. The critic is the
inverse and the keystone: ONE strong-model pass that judges the draft against a POSITIVE
rubric — grounded, responsive to the lead, a sound next sales move, objection handled, right
register — and approves only what clears it. It runs on EVERY reply, not just risky-looking
ones, and it FAILS CLOSED: a draft it can't approve after one rewrite (or an error while
checking) is handed to a human, never sent. That is what turns 'absence of the specific
fabrications we've been burned by' into 'a reply proven good before it goes out'."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.ports.llm import LLMPort

from .needs import NeedsProfile

logger = logging.getLogger(__name__)

# The five dimensions a top-tier sales reply must satisfy. Kept as data (not prose buried in
# the prompt) so the same list drives the rubric text AND stays greppable/testable.
DIMENSIONS = (
    ("grounded", "Every concrete fact (price, date, schedule, duration, platform, link, "
     "discount, certificate, statistic, alumni/success story) is supported by the KNOWLEDGE "
     "BASE — which includes BOTH the full focus card AND the one-line QUICK FACTS of every "
     "other product in the catalog. A number is grounded if the SAME VALUE appears anywhere in "
     "the KB in ANY Indonesian format: 'Rp 13 juta' = '13jt' = 'Rp 13.000.000'; '500 ribu' = "
     "'500rb' = '500.000'; 'cicil 4x' = '4×3.250.000'. Do NOT fail a fact merely for a "
     "formatting or abbreviation difference — only fail when the value is ABSENT from the KB or "
     "DIFFERENT from what the KB says (a wrong figure, an invented date/discount/claim). When "
     "unsure whether a value is in the KB, scan the catalog lines before failing. A faithful "
     "PARAPHRASE of a card fact is grounded, not a fabrication: 'kelasnya hybrid', 'bisa offline "
     "atau online', 'via Teams' are grounded when the card's format line says so. Fail grounded "
     "ONLY for a SPECIFIC invented value (a price/date/discount/number/name/link that is wrong "
     "or absent) — never for a general description that matches the card."),
    ("responsive", "The reply directly addresses what the lead's LAST message actually said or "
     "asked. Answering a different question, ignoring their point, or a generic reaction "
     "('Mantap Kak!') that doesn't engage their message FAILS."),
    ("sales_move", "The reply advances the sale by ONE sound step: it answers/adds value AND "
     "carries a clear next step or one engaging question. Rambling, stalling, repeating an "
     "earlier message, or dropping the thread with no forward motion FAILS."),
    ("objection", "If the lead raised an objection or hesitation (too expensive, no time, "
     "distrust, 'will it get me a job', 'I'll think about it', too far, 'I don't get it'), the "
     "reply actually HANDLES it — acknowledges and reframes with a real benefit — before any "
     "pitch or contact ask. Pitching over an un-addressed objection FAILS."),
    ("register", "Right language (mirrors the lead's language) and warm 'Kak'/'aku' register, "
     "at most ONE question, no premature demand for a phone/WhatsApp from a still-cold lead, "
     "no promise the channel can't keep (voice note, call, WhatsApp delivery). NOTE: '|||' is "
     "the intended separator between message bubbles — treat it as a normal bubble break, NEVER "
     "a defect."),
)

_RUBRIC = "\n".join(f"- {name}: {desc}" for name, desc in DIMENSIONS)

_CRITIC_SYSTEM = (
    "You are a demanding sales-floor manager reviewing ONE draft reply a bot is about to send "
    "a lead in Instagram Direct. You decide if it is good enough to send. Judge it against "
    "EXACTLY these five dimensions:\n"
    f"{_RUBRIC}\n\n"
    "You are given: the KNOWLEDGE BASE (the only allowed source of facts), KNOWN LEAD NEEDS & "
    "OPEN OBJECTIONS, the recent DIALOG (the bot's own prior lines included, so you can catch "
    "repetition), the lead's LAST MESSAGE, and the DRAFT.\n"
    "Be strict but fair: a genuinely good, grounded, responsive reply PASSES — do not invent "
    "nitpicks. But any real failure on any dimension means the draft is NOT good enough.\n"
    "A deliberate honest hand-off ('let me confirm with the team') is acceptable ONLY when the "
    "lead's question truly has no answer in the KNOWLEDGE BASE; if the KB answers it, deferring "
    "FAILS 'responsive'.\n"
    'Return ONLY this JSON: {"ok": bool, "failures": [str], "fix": str}. '
    "failures: one short string per failed dimension, prefixed with its name and the specific "
    "problem (e.g. \"grounded: quotes Rp 7jt, KB card says Rp 1.882.955\", \"objection: lead "
    "said 'mahal', reply just re-pitches the price\"). Empty list when ok=true. "
    "fix: when ok=false, ONE concrete instruction the writer can follow to fix ALL failures in "
    "a rewrite; empty string when ok=true.")


@dataclass(frozen=True)
class Critique:
    ok: bool
    failures: list[str] = field(default_factory=list)
    fix: str = ""
    errored: bool = False  # the check itself failed (broker error/bad JSON) — treat as NOT ok

    def summary(self) -> str:
        return "; ".join(self.failures[:5]) or ("error" if self.errored else "ok")


def _prior_bot_lines(dialog: list, limit: int = 6) -> str:
    """The bot's own recent messages, so the critic can catch a repeat of something already
    said — the failure mode a fresh single-turn judge would otherwise miss."""
    out = [(m.text or "").strip() for m in dialog if getattr(m, "direction", "") == "out"]
    return "\n".join(f"- {t}" for t in out[-limit:] if t)


def _needs_and_objections(needs: NeedsProfile, open_objections: list[str]) -> str:
    lines: list[str] = []
    if needs.pains:
        lines.append("pains: " + "; ".join(needs.pains))
    if needs.gains:
        lines.append("gains: " + "; ".join(needs.gains))
    if open_objections:
        lines.append("OPEN OBJECTIONS (must be handled before any pitch): "
                     + "; ".join(open_objections))
    return "\n".join(lines) or "(nothing captured yet)"


def _parse_critique(raw: str) -> Critique:
    """The critic's JSON verdict → Critique. A non-JSON body is itself a failed check: we can't
    confirm the reply is good, so fail closed rather than guess it passed."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        logger.warning("critic: unparseable verdict %r", (raw or "")[:200])
        return Critique(ok=False, errored=True)
    if not isinstance(data, dict):
        return Critique(ok=False, errored=True)
    failures = [str(f).strip() for f in (data.get("failures") or []) if str(f).strip()][:8]
    ok = bool(data.get("ok")) and not failures
    return Critique(ok=ok, failures=failures, fix=str(data.get("fix") or "").strip())


async def critique_reply(
    llm: LLMPort, *, reply: str, last_inbound: str, dialog: list, context: str,
    needs: NeedsProfile, open_objections: list[str], lang: str,
    branch_id: int, thread_id: int, bill: bool = True,
) -> Critique:
    """Judge one draft against the five-dimension rubric. Returns a Critique; on any broker or
    parse error returns Critique(ok=False, errored=True) so the caller fails CLOSED — the
    opposite of guard.verify_grounding, which fails open. A reply we couldn't verify is a
    reply we don't send."""
    user = (
        f"KNOWLEDGE BASE:\n{context[:14000]}\n\n"
        f"KNOWN LEAD NEEDS & OPEN OBJECTIONS:\n{_needs_and_objections(needs, open_objections)}\n\n"
        f"RECENT DIALOG (bot's prior lines):\n{_prior_bot_lines(dialog) or '(none yet)'}\n\n"
        f"LEAD'S LAST MESSAGE:\n{last_inbound or '(they only tapped an ad / sent no words)'}\n\n"
        f"REPLY LANGUAGE EXPECTED: {lang}\n\n"
        f"DRAFT:\n{reply}")
    messages = [
        {"role": "system", "content": _CRITIC_SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        raw, meta = await llm.chat(
            messages, capability="chat:smart", require_json_schema=True,
            workflow="critic", thread_id=thread_id, branch_id=branch_id)
        if not bill:
            meta.pop("cost_usd", None)
        return _parse_critique(raw)
    except Exception as exc:  # noqa: BLE001 — an errored check must fail CLOSED, never pass
        logger.warning("critic failed branch=%d thread=%d: %s", branch_id, thread_id, exc)
        return Critique(ok=False, errored=True)


# Stamped onto a critic-forced hand-off (draft rejected twice) so the alert and chat log show
# WHY, not a misattributed model stage_reason. Flows into the alert body and the ThreadLog.
CRITIC_HANDOFF_REASON = (
    "Степан не смог составить ответ топ-уровня (критик-гейт отклонил черновик дважды) — "
    "нужен ручной ответ менеджера")

CRITIC_CORRECTION = (
    "[System: a sales-floor reviewer rejected your previous draft for these reasons: {failures}. "
    "Fix ALL of them in a rewrite. {fix} "
    "Keep every fact grounded in the knowledge base, directly address what the lead just said, "
    "handle any objection before pitching, and move the sale one clear step forward. Return the "
    "JSON as usual.]")

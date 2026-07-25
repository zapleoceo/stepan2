"""Reading the lead is a separate job from selling to them, so it gets its own model call.

This started as a backstop for the main turn, which was asked to write a warm reply AND fill
a 21-field dossier at once. Measured live (2026-07-23): of 1215 leads active in 7 days, only
~5% had ANY dossier saved and ~2% had pains+desired_state — the reply wins that competition
for attention every time. On 2026-07-25 it stopped being a backstop and became the owner:
the selling model no longer carries the dossier at all, and this call runs on every turn.

Why that is better rather than merely cheaper: the extractor has no competing task. It reads
a transcript and answers one question — what did this person reveal about themselves — with
no reply to compose, no stage to choose, no register to hold. The selling model gets the same
attention back for the thing only it can do.

Runs on chat:fast (free) and never blocks the reply: any failure — broker error, timeout,
unparseable JSON — is swallowed and logged. An unreachable extractor costs a turn of learning,
never the lead's answer.
"""
from __future__ import annotations

import json
import logging

from app.adapters.db.models import Message
from app.ports.llm import LLMPort

from .decision import str_list, strip_fences
from .dossier import LeadDossier, Objection
from .prompt import _role_of
from .routing import FAST
from .signals import AD_TEMPLATE_RE

logger = logging.getLogger(__name__)

_DIALOG_BUDGET = 20  # last N turns — discovery lives in recent talk, not the whole history

_SYSTEM = """\
You read one Instagram DM conversation between a lead and a sales rep at an IT school. Your \
ONLY job: report what the LEAD revealed about themselves — their own meaning, not what the \
rep suggested or offered. Capture it whenever the lead DESCRIBES a situation, a problem, a \
wish, a fear, a constraint or a reason — you do NOT need the exact word, and you do NOT need \
a full sentence; a short phrase in their own words is enough. Paraphrase tightly into \
Indonesian. A bare "iya"/"ok"/"boleh" with no content of its own reveals nothing. Never \
invent anything they did not say. Report only what is NEW — don't repeat what is already \
listed as known below. Nothing new: return empty values.

job_to_be_done: WHY they're here now — the task they want done.
pains: what's not working now, what worries them, what holds them back (incl. fears like \
"takut nggak bisa coding").
desired_state: the outcome they want — the goal, not the product.
objections: a reason to hesitate (price/time/trust/parents/…), in their words. Empty if none.
role: school|student|working|jobseeking|parent — only if they said so. Empty otherwise.
readiness: exploring|considering|ready — how close they are to committing, from THEIR words. \
"ready" only if they asked to enrol/pay or gave contact details to be called. Empty if unclear.
refusal: none|soft|vague|blunt. soft = "nanti saya pikirkan", "belum sekarang" (a polite no \
with a reason); vague = they closed the conversation politely with no reason; blunt = an \
explicit "no"/"stop"/"not interested". Default none.
payment_preference: e.g. "cicilan", "bayar penuh" — only if they raised it.
budget_signal: what they said about money being a problem — "mahal", "belum ada budget", \
"masih pelajar". Empty if they never raised it.

Examples (lead line -> what to capture):
- "biar bisa terima order online, sekarang masih manual ribet" -> pains:["proses order \
masih manual dan ribet"], desired_state:["bisa terima order online"]
- "takutnya aku nggak bisa coding" -> pains:["takut nggak bisa coding"]
- "mahal banget ya" -> objections:["harga terlalu mahal"], budget_signal:"harga terasa mahal"
- "saya masih kuliah, cari sampingan" -> role:"student", job_to_be_done:"cari penghasilan \
sampingan sambil kuliah"
- "nanti saya pikirkan dulu ya kak" -> refusal:"soft"
- "boleh minta nomor rekeningnya?" -> readiness:"ready"
- "iya kak" -> nothing

Return ONLY this JSON, no prose, no markdown fences:
{"job_to_be_done": str, "pains": [str], "desired_state": [str], "objections": [str], \
"role": str, "readiness": str, "refusal": str, "payment_preference": str, "budget_signal": str}
"""


def _transcript(dialog: list[Message]) -> str:
    lines = []
    for m in dialog[-_DIALOG_BUDGET:]:
        text = (m.text or "").strip()
        if not text:
            continue
        speaker = "LEAD" if _role_of(m) == "user" else "REP"
        if speaker == "LEAD" and AD_TEMPLATE_RE.match(text):
            # The ad's own prefilled button text, not the lead's words (thread 5025: the
            # backfill lifted "Boleh info jadwal, durasi, dan biaya?" verbatim into
            # job_to_be_done as if the lead had said it). Drop it entirely rather than let
            # the model treat a tap as a revealed goal.
            continue
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _known_block(dossier: LeadDossier) -> str:
    lines = [f"- job_to_be_done: {dossier.job_to_be_done}" if dossier.job_to_be_done else "",
             f"- pains: {'; '.join(dossier.pains)}" if dossier.pains else "",
             f"- desired_state: {'; '.join(dossier.desired_state)}"
             if dossier.desired_state else ""]
    body = "\n".join(line for line in lines if line)
    if body:
        return f"ALREADY KNOWN (do not repeat these):\n{body}"
    return "ALREADY KNOWN: nothing yet."


async def extract_discovery(  # noqa: PLR0913
    llm: LLMPort, dialog: list[Message], dossier: LeadDossier, lang: str,
    branch_id: int, thread_id: int, budget: object = None,
) -> LeadDossier:
    """The dialog's discovery delta, extracted on chat:fast. Empty LeadDossier on any failure —
    the caller merges this straight into what's already known via merge_dossier, so a soft
    failure here simply means this turn adds nothing, never that the turn breaks."""
    transcript = _transcript(dialog)
    if not transcript:
        return LeadDossier()
    user = f"{_known_block(dossier)}\n\nCONVERSATION (lang: {lang}):\n{transcript}"
    try:
        raw, meta = await llm.chat(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            capability=FAST, require_json_schema=True,
            workflow="discovery", thread_id=thread_id, branch_id=branch_id)
        if budget is not None:
            await budget.record(float(meta.get("cost_usd") or 0.0))
        return _parse(raw)
    except Exception as exc:  # noqa: BLE001 — an unreachable extractor must not cost the reply
        logger.warning("discovery unavailable branch=%d thread=%d: %s — skipped",
                       branch_id, thread_id, exc)
        return LeadDossier()


_ROLES = frozenset({"school", "student", "working", "jobseeking", "parent"})
_READINESS = frozenset({"exploring", "considering", "ready"})
_REFUSALS = frozenset({"none", "soft", "vague", "blunt"})


def _one_of(value: object, allowed: frozenset[str]) -> str:
    """An enum the extractor may only fill with a known value — a stray label would silently
    change routing (readiness/refusal both feed pick_capability), so drop what we don't know."""
    text = str(value or "").strip().lower()
    return text if text in allowed else ""


def _parse(raw: str) -> LeadDossier:
    try:
        data = json.loads(strip_fences(raw))
    except (json.JSONDecodeError, TypeError):
        logger.warning("discovery: unparseable extraction — skipped")
        return LeadDossier()
    if not isinstance(data, dict):
        return LeadDossier()
    objections = [Objection(text) for text in str_list(data.get("objections"))]
    return LeadDossier(
        job_to_be_done=str(data.get("job_to_be_done") or "").strip(),
        pains=str_list(data.get("pains")),
        desired_state=str_list(data.get("desired_state")),
        objections=objections,
        role=_one_of(data.get("role"), _ROLES),
        readiness=_one_of(data.get("readiness"), _READINESS),
        refusal=_one_of(data.get("refusal"), _REFUSALS) or "none",
        payment_preference=str(data.get("payment_preference") or "").strip(),
        budget_signal=str(data.get("budget_signal") or "").strip(),
    )

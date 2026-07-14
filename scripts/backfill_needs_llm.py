"""One-off: LLM re-extraction of jobs/pains/gains from a lead's FULL transcript.

The live collector extracts per-turn, so a pain a lead voiced many turns ago (now out of the
model's context window) is never recovered unless they re-raise it (thread 1081). This reads a
lead's WHOLE conversation at once and re-derives a consolidated Value Proposition Canvas.

Reads ONLY the lead's own inbound messages — never the bot's (thread 2912: needs invented from
the bot's own suggestions). Merges (never replaces) so nothing is lost; the new near-dup
collapse in needs.py keeps the result clean.

Run (transient, not billed to reply metrics):
    docker exec -i stepan2-api python - --dry-run --limit=5 < scripts/backfill_needs_llm.py
    docker exec -i stepan2-api python -                     < scripts/backfill_needs_llm.py   # apply, all active
    docker exec -i stepan2-api python - --all               < scripts/backfill_needs_llm.py   # incl. dormant/paused
"""
from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.needs import merge_needs, parse_needs

DRY = "--dry-run" in sys.argv
ACTIVE_ONLY = "--all" not in sys.argv
LIMIT = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--limit=")), None)
BRANCH = 1

_SYSTEM = (
    "You read ONLY a lead's own messages from a whole sales conversation and extract PAINS - "
    "the fears, doubts, obstacles, or cost-of-inaction the lead genuinely voiced. Output STRICT "
    'JSON: {"pains":[...]}. A pain is an emotional blocker or worry, e.g. "takut nggak bisa / '
    'nggak kekejar", "nggak punya laptop / waktu / uang", "takut buang duit", "ragu cocok apa '
    'nggak", "dibimbing sampai bener-bener bisa nggak?" (fear of not reaching the goal). '
    "A neutral factual QUESTION is NOT a pain: 'berapa harganya?', 'gratis nggak?', 'ada "
    "sertifikat?', 'gimana caranya?', 'jadwalnya kapan?' are information requests, not pains. "
    "Logistics ('terima brosur', 'minta link', 'siap datang', 'minta nomor') are NOT pains. "
    "Short phrases in the lead's OWN words - never invent, never infer from a course name. "
    'At most 3, no near-duplicates. If the lead voiced no real pain, return {"pains":[]}.'
)

_SQL = (
    "SELECT l.id, l.needs FROM lead l "
    "WHERE l.branch_id = :b AND l.needs ~ '^\\s*\\{' "
    "  AND jsonb_array_length(coalesce(l.needs::jsonb->'gains','[]')) > 0 "
    "  AND jsonb_array_length(coalesce(l.needs::jsonb->'pains','[]')) = 0 "
    "  {active} ORDER BY l.id"
)


def _lst(v: object) -> list[str]:
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def _parse_json(raw: str) -> dict:
    s = (raw or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    a, b = s.find("{"), s.rfind("}")
    return json.loads(s[a:b + 1]) if a >= 0 and b > a else {}


async def _lead_transcript(s, lead_id: int) -> str:
    rows = (await s.execute(text(
        "SELECT m.text FROM message m JOIN channel_thread ct ON ct.id = m.thread_id "
        "WHERE ct.lead_id = :lid AND m.sent_by = 'lead' AND coalesce(m.text,'') <> '' "
        "ORDER BY m.occurred_at"), {"lid": lead_id})).all()
    return "\n".join(f"- {r[0].strip()}" for r in rows if r[0] and r[0].strip())[:6000]


async def main() -> None:
    llm = BrokerLLM()
    active = ("AND l.agent_enabled AND NOT l.is_blocked "
              "AND l.stage NOT IN ('dormant','handed_off')") if ACTIVE_ONLY else ""
    async with session_scope() as s:
        rows = (await s.execute(text(_SQL.replace("{active}", active)), {"b": BRANCH})).all()
        if LIMIT:
            rows = rows[:LIMIT]
        print(f"targets={len(rows)} dry_run={DRY} active_only={ACTIVE_ONLY}")
        updated = recovered = 0
        for lead_id, raw in rows:
            transcript = await _lead_transcript(s, lead_id)
            if not transcript:
                continue
            try:
                out, _ = await llm.chat(
                    [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": "LEAD MESSAGES:\n" + transcript}],
                    capability="chat:fast", temperature=0.2, workflow="backfill", branch_id=BRANCH)
                d = _parse_json(out)
            except Exception as e:  # noqa: BLE001 — one bad lead must not abort the batch
                print(f"[{lead_id}] SKIP ({type(e).__name__}: {e})")
                continue
            stored = parse_needs(raw)
            # Only PAINS are recovered — jobs/gains were already cleaned by the deterministic
            # pass and re-extracting them risks logistics/questions polluting the profile.
            merged = merge_needs(stored, [], _lst(d.get("pains")), [], stored.discovery_complete)
            if merged.pains and not stored.pains:
                recovered += 1
            print(f"[{lead_id}] pains {stored.pains} -> {merged.pains}")
            if not DRY and merged.to_json() != (raw or "").strip():
                await s.execute(text("UPDATE lead SET needs = :n WHERE id = :i"),
                                {"n": merged.to_json(), "i": lead_id})
                updated += 1
        print(f"pains_recovered={recovered} updated={updated}")


asyncio.run(main())

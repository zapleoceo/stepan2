"""One-off: classify lead.lead_type from the chat for every lead, using ONLY the lead's own
messages. Backfills the segment classification that otherwise only gets set going forward as a
side-effect of a live reply, so historical leads stay NULL/"unclear".

Run in the container:  python -m scripts.reclassify_lead_types [--branch N] [--limit N] [--dry]

Same 7 types as the live decision prompt (kept verbatim below so the backfill matches). Leads
with no dialog are left untouched (no signal). Each lead is its own transaction, so a crash
resumes cleanly - already-processed leads just get recomputed, which is idempotent."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reclassify_lead_types")

_TYPES = ("hot", "warm", "cold", "no_budget", "student", "non_target", "unclear")

# Verbatim from app/modules/conversation/prompt.py LEAD TYPE block, so the backfill agrees with
# whatever the live classifier would have emitted turn-by-turn.
_SYSTEM = (
    "Below are ONLY the LEAD's own messages from an Instagram sales chat for IT STEP (a coding "
    "school in Jakarta). The agent's replies are NOT included. Classify WHAT KIND of lead this "
    "is. Read intent honestly - a polite 'iya'/'ok' is not real interest. Emit exactly ONE type; "
    "use 'unclear' until there are ~3 messages of real signal.\n"
    "- 'hot': explicit intent to enrol / pay / reserve NOW, or 'cara daftar / mau ikut / gimana "
    "bayar'.\n"
    "- 'warm': genuine interest, engaged, a real need surfaced, no blocker.\n"
    "- 'cold': low intent - vague or one-word replies, 'cuma lihat / nanya', browsing, no chosen "
    "direction after a couple of turns.\n"
    "- 'no_budget': wants it but can't/won't pay - 'gapunya duit', price shock ('kirain 100k'), "
    "no income.\n"
    "- 'student': still at school / a minor / a structural blocker (no phone at pondok, no way to "
    "pay), regardless of how interested they sound.\n"
    "- 'non_target': wrong audience (asks for something we don't teach), off-topic, trolling, or "
    "an explicit 'I don't want it'.\n"
    "- 'unclear': not enough signal yet.\n"
    'Output ONLY this JSON, nothing else:\n{"lead_type": "warm"}'
)


async def _lead_ids(
    session, branch: int | None, limit: int | None, offset: int = 0,
) -> list[int]:
    where = "WHERE l.branch_id = :b" if branch else ""
    params: dict = {"b": branch} if branch else {}
    q = f"SELECT l.id FROM lead l {where} ORDER BY l.id"  # noqa: S608 — where is a fixed clause
    if limit:
        q += " LIMIT :lim"
        params["lim"] = limit
    if offset:
        q += " OFFSET :off"
        params["off"] = offset
    rows = (await session.execute(text(q), params)).all()
    return [r[0] for r in rows]


async def _dialog(session, lead_id: int) -> str:
    rows = (await session.execute(
        text("SELECT m.text FROM message m"
             " JOIN channel_thread ct ON ct.id = m.thread_id"
             " WHERE ct.lead_id = :lid AND m.direction = 'in' AND m.text <> ''"
             " ORDER BY m.occurred_at, m.id LIMIT 200"),
        {"lid": lead_id},
    )).all()
    return "\n".join((r[0] or "").strip() for r in rows if (r[0] or "").strip())[:8000]


def _parse(raw: str) -> str | None:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        s = s[4:] if s[:4].lower() == "json" else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        val = str(json.loads(s[i:j + 1]).get("lead_type", "")).strip().lower()
    except (json.JSONDecodeError, AttributeError):
        return None
    return val if val in _TYPES else None


async def _process(lead_id: int, llm: BrokerLLM, dry: bool) -> str:
    async with session_scope() as session:
        convo = await _dialog(session, lead_id)
        if not convo:
            return "empty"
        raw = None
        for attempt in range(4):  # broker 502/ReadTimeout under load — retry with backoff
            try:
                raw, _ = await llm.chat(
                    [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": convo}],
                    capability="chat:smart", max_tokens=1500, workflow="reclassify",
                    thread_id=lead_id)
                if raw and raw.strip():
                    break
                raw = None
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    log.warning("lead %s: LLM failed after retries: %s", lead_id, exc)
                    return "error"
                await asyncio.sleep(2 * (attempt + 1))
        if raw is None:
            return "error"
        ltype = _parse(raw)
        if ltype is None:
            return "error"
        if not dry:
            await session.execute(
                text("UPDATE lead SET lead_type = :t WHERE id = :id"),
                {"t": ltype, "id": lead_id})
        return ltype


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    async with session_scope() as session:
        ids = await _lead_ids(session, args.branch, args.limit, args.offset)
    log.info("reclassifying %d leads (dry=%s, conc=%d)", len(ids), args.dry, args.concurrency)
    llm = BrokerLLM()
    sem = asyncio.Semaphore(args.concurrency)
    counts: dict[str, int] = {}

    async def run(lid: int, idx: int) -> None:
        async with sem:
            r = await _process(lid, llm, args.dry)
            counts[r] = counts.get(r, 0) + 1
            if idx % 25 == 0:
                log.info("… %d/%d  %s", idx, len(ids),
                         " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    await asyncio.gather(*(run(lid, i + 1) for i, lid in enumerate(ids)))
    log.info("DONE  %s", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    asyncio.run(main())

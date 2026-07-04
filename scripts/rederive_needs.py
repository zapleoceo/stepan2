"""One-off: wipe every lead.needs and re-derive it from the chat, capturing ONLY what the
lead explicitly said in their own words — never the agent's suggestions, never inference.

Run in the container:  python -m scripts.rederive_needs [--branch N] [--limit N] [--dry]

Leads with no dialog are cleared to NULL. Each lead is its own transaction, so a crash
resumes cleanly (already-processed leads just get recomputed, which is idempotent)."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.needs import NeedsProfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rederive_needs")

_SYSTEM = (
    "You extract a sales lead's needs from an Instagram DM chat. Use ONLY what the LEAD "
    "said in THEIR OWN words. ABSOLUTE RULES:\n"
    "- NEVER copy the agent's suggestions or questions. If the agent listed options "
    "('become an analyst? build apps?') and the lead only replied 'yes'/'iya'/'everything'/"
    "a single word, that is NOT the lead stating those things — capture at most ONE short "
    "vague job, or nothing.\n"
    "- NEVER infer, guess, or invent. If the lead didn't explicitly voice something, leave "
    "it out. Empty lists are correct and expected.\n"
    "- Keep the lead's own language and wording; short phrases.\n"
    "Output ONLY this JSON, nothing else:\n"
    '{"jobs":[],"pains":[],"gains":[],"discovery_complete":false}\n'
    "jobs = what the lead explicitly wants to achieve. pains = fears/obstacles/cost-of-"
    "inaction the lead explicitly voiced. gains = outcomes the lead explicitly said they "
    "want. discovery_complete = true ONLY if the lead voiced a real pain in their own words."
)


async def _lead_ids(session, branch: int | None, limit: int | None) -> list[int]:
    where = "WHERE l.branch_id = :b" if branch else ""
    params: dict = {"b": branch} if branch else {}
    q = f"SELECT l.id FROM lead l {where} ORDER BY l.id"  # noqa: S608 — where is a fixed clause
    if limit:
        q += " LIMIT :lim"
        params["lim"] = limit
    rows = (await session.execute(text(q), params)).all()
    return [r[0] for r in rows]


async def _dialog(session, lead_id: int) -> str:
    rows = (await session.execute(
        text("SELECT m.direction, m.text FROM message m"
             " JOIN channel_thread ct ON ct.id = m.thread_id"
             " WHERE ct.lead_id = :lid AND m.text <> ''"
             " ORDER BY m.occurred_at, m.id LIMIT 200"),
        {"lid": lead_id},
    )).all()
    return "\n".join(
        f"{'LEAD' if r[0] == 'in' else 'AGENT'}: {(r[1] or '').strip()}"
        for r in rows if (r[1] or "").strip()
    )[:8000]


def _parse(raw: str) -> NeedsProfile:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        s = s[4:] if s[:4].lower() == "json" else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return NeedsProfile()
    try:
        d = json.loads(s[i:j + 1])
    except json.JSONDecodeError:
        return NeedsProfile()
    def lst(v: object) -> list[str]:
        return [str(x).strip() for x in v][:6] if isinstance(v, list) else []
    return NeedsProfile(jobs=lst(d.get("jobs")), pains=lst(d.get("pains")),
                        gains=lst(d.get("gains")),
                        discovery_complete=bool(d.get("discovery_complete")))


async def _process(lead_id: int, llm: BrokerLLM, dry: bool) -> str:
    async with session_scope() as session:
        convo = await _dialog(session, lead_id)
        if not convo:
            if not dry:
                await session.execute(text("UPDATE lead SET needs = NULL WHERE id = :id"),
                                      {"id": lead_id})
            return "empty"
        try:
            raw, _ = await llm.chat(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": convo}],
                capability="chat:fast", max_tokens=400, workflow="rederive", thread_id=lead_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("lead %s: LLM failed: %s", lead_id, exc)
            return "error"
        profile = _parse(raw)
        if not dry:
            await session.execute(text("UPDATE lead SET needs = :n WHERE id = :id"),
                                  {"n": profile.to_json(), "id": lead_id})
        return "ok"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    async with session_scope() as session:
        ids = await _lead_ids(session, args.branch, args.limit)
    log.info("re-deriving needs for %d leads (dry=%s, conc=%d)", len(ids), args.dry,
             args.concurrency)
    llm = BrokerLLM()
    sem = asyncio.Semaphore(args.concurrency)
    counts = {"ok": 0, "empty": 0, "error": 0}

    async def run(lid: int, idx: int) -> None:
        async with sem:
            r = await _process(lid, llm, args.dry)
            counts[r] += 1
            if idx % 25 == 0:
                log.info("… %d/%d  ok=%d empty=%d error=%d",
                         idx, len(ids), counts["ok"], counts["empty"], counts["error"])

    await asyncio.gather(*(run(lid, i + 1) for i, lid in enumerate(ids)))
    log.info("DONE  ok=%d empty=%d error=%d", counts["ok"], counts["empty"], counts["error"])


if __name__ == "__main__":
    asyncio.run(main())

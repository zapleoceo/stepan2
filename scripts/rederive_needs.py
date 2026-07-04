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
    "You extract a sales lead's needs from an Instagram DM chat, from what the LEAD said in "
    "THEIR OWN words.\n"
    "CAPTURE anything the lead EXPLICITLY stated themselves, e.g. 'I want to be a fullstack "
    "developer', 'pengen bikin aplikasi mobile game', 'I want to switch careers' — those ARE "
    "explicit jobs/gains and MUST be captured in the lead's words.\n"
    "DO NOT capture: (a) the AGENT's suggestions/options the lead only replied 'yes'/'iya'/"
    "'everything'/a single word to — a bare yes is NOT the lead stating those; (b) anything "
    "you infer or invent that the lead never actually said.\n"
    "Rank by importance and keep only the few that matter (<=3 each); short phrases in the "
    "lead's language; empty lists when the lead truly said nothing concrete.\n"
    "Output ONLY this JSON, nothing else:\n"
    '{"jobs":[],"pains":[],"gains":[],"discovery_complete":false}\n'
    "jobs = what the lead explicitly wants to achieve. pains = fears/obstacles/cost-of-"
    "inaction the lead explicitly voiced. gains = outcomes the lead explicitly said they "
    "want. discovery_complete = true ONLY if the lead voiced a real pain in their own words."
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
        raw = None
        for attempt in range(4):  # broker 502/ReadTimeout under load — retry with backoff
            try:
                # chat:smart + a big token budget: the routed model is a REASONING model
                # (gpt-oss-120b) that spends tokens thinking, so a small max_tokens returns
                # an EMPTY completion — the bug that silently blanked most profiles on the
                # first pass. 1500 leaves room for the JSON after the reasoning.
                raw, _ = await llm.chat(
                    [{"role": "system", "content": _SYSTEM},
                     {"role": "user", "content": convo}],
                    capability="chat:smart", max_tokens=1500, workflow="rederive",
                    thread_id=lead_id)
                if raw and raw.strip():
                    break
                raw = None  # empty completion → treat as a retryable failure
            except Exception as exc:  # noqa: BLE001
                if attempt == 3:
                    log.warning("lead %s: LLM failed after retries: %s", lead_id, exc)
                    return "error"
                await asyncio.sleep(2 * (attempt + 1))
        if raw is None:
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
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    async with session_scope() as session:
        ids = await _lead_ids(session, args.branch, args.limit, args.offset)
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

"""One-off: re-check every lead's funnel STAGE against its dialog and correct it, using a
focused stage classifier. RELABEL ONLY — writes lead.stage + a stage_event audit row and
NOTHING else: no replies are sent, no manager hand-offs are raised, agent_enabled and the
follow-up timer are left untouched. It never assigns ready / handed_off / manager — a batch
must not trigger a live hand-off — and it skips leads already in those stages or blocked.

Run in the container:  python -m scripts.restage_leads [--branch N] [--limit N] [--dry]

Each lead is its own transaction, so a crash resumes cleanly (idempotent recompute)."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("restage_leads")

# The batch may only place a lead in a non-terminal, non-human stage. ready/handed_off/manager
# are live hand-offs (bot off, manager card, CAPI) — never fired from a relabel pass.
_STAGES = ("new", "nurturing", "qualifying", "presenting", "objection", "dormant")
_EXCLUDED = ("ready", "handed_off", "manager")

_SYSTEM = (
    "Below is a sales chat between a LEAD and the AGENT (Stepan) for IT STEP, a coding school "
    "in Jakarta. Decide WHICH FUNNEL STAGE the lead is in right now, from the flow of the whole "
    "conversation. Output exactly ONE stage:\n"
    "- 'new': the lead only just arrived — no substantive back-and-forth yet.\n"
    "- 'nurturing': cold / undecided — hasn't accepted that coding is for them; being warmed up, "
    "no real need surfaced.\n"
    "- 'qualifying': discovery in progress — the agent is digging for the lead's goal / pain, a "
    "concrete need is NOT captured yet. This is the default working stage.\n"
    "- 'presenting': a real need is on the table and the agent is pitching / has pitched the "
    "product (price, format, syllabus) against it.\n"
    "- 'objection': the lead is pushing back (price, doubt, timing) and the agent is handling it.\n"
    "- 'dormant': the lead EXPLICITLY stopped / refused ('gak jadi', 'stop', 'no thanks', 'nanti "
    "aja') or clearly disengaged. Do NOT infer dormant merely from a short chat — only from an "
    "explicit stop or refusal.\n"
    "Judge by what actually happened, not by politeness. A polite 'iya' with no real need is "
    "still qualifying, not presenting.\n"
    'Output ONLY this JSON, nothing else:\n{"stage": "qualifying"}'
)


async def _lead_rows(
    session, branch: int | None, limit: int | None, offset: int = 0,
) -> list[tuple[int, int, str, int]]:
    where = ["l.stage NOT IN ('ready','handed_off','manager')", "l.is_blocked = false"]
    params: dict = {}
    if branch:
        where.append("l.branch_id = :b")
        params["b"] = branch
    # WHERE is built only from the fixed clauses above; all values are bound params.
    subq = "(SELECT ct.id FROM channel_thread ct WHERE ct.lead_id = l.id ORDER BY ct.id LIMIT 1)"
    q = f"SELECT l.id, l.branch_id, l.stage, {subq} FROM lead l WHERE {' AND '.join(where)} ORDER BY l.id"  # noqa: S608, E501
    if limit:
        q += " LIMIT :lim"
        params["lim"] = limit
    if offset:
        q += " OFFSET :off"
        params["off"] = offset
    rows = (await session.execute(text(q), params)).all()
    return [(r[0], r[1], str(r[2]), r[3]) for r in rows if r[3] is not None]


async def _dialog(session, lead_id: int) -> str:
    rows = (await session.execute(
        text("SELECT m.direction, m.text FROM message m"
             " JOIN channel_thread ct ON ct.id = m.thread_id"
             " WHERE ct.lead_id = :lid AND m.text <> ''"
             " ORDER BY m.occurred_at, m.id LIMIT 200"),
        {"lid": lead_id},
    )).all()
    lines = [f"{'LEAD' if d == 'in' else 'AGENT'}: {(t or '').strip()}"
             for d, t in rows if (t or "").strip()]
    return "\n".join(lines)[:8000]


def _parse(raw: str) -> str | None:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        s = s[4:] if s[:4].lower() == "json" else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        val = str(json.loads(s[i:j + 1]).get("stage", "")).strip().lower()
    except (json.JSONDecodeError, AttributeError):
        return None
    return val if val in _STAGES else None


async def _process(row: tuple[int, int, str, int], llm: BrokerLLM, dry: bool) -> str:
    lead_id, branch_id, cur_stage, thread_id = row
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
                    capability="chat:smart", max_tokens=1500, workflow="restage",
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
        new_stage = _parse(raw)
        if new_stage is None:
            return "error"
        if new_stage == cur_stage:
            return "same"
        if new_stage in _EXCLUDED:  # defensive — classifier can't emit these, never hand off
            return "same"
        if not dry:
            await session.execute(
                text("UPDATE lead SET stage = :s WHERE id = :id"),
                {"s": new_stage, "id": lead_id})
            await session.execute(
                text("INSERT INTO stage_event"
                     " (branch_id, lead_id, thread_id, from_stage, to_stage, actor, reason,"
                     "  created_at)"
                     " VALUES (:b, :l, :t, :f, :s, 'batch:restage', 'full stage re-check', now())"),
                {"b": branch_id, "l": lead_id, "t": thread_id, "f": cur_stage, "s": new_stage})
        return f"{cur_stage}->{new_stage}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    async with session_scope() as session:
        rows = await _lead_rows(session, args.branch, args.limit, args.offset)
    log.info("re-checking stage for %d leads (dry=%s, conc=%d)", len(rows), args.dry,
             args.concurrency)
    llm = BrokerLLM()
    sem = asyncio.Semaphore(args.concurrency)
    counts: dict[str, int] = {}

    async def run(row: tuple[int, int, str, int], idx: int) -> None:
        async with sem:
            r = await _process(row, llm, args.dry)
            bucket = "moved" if "->" in r else r
            counts[bucket] = counts.get(bucket, 0) + 1
            if idx % 50 == 0:
                log.info("… %d/%d  %s", idx, len(rows),
                         " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    await asyncio.gather(*(run(row, i + 1) for i, row in enumerate(rows)))
    log.info("DONE  %s", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    asyncio.run(main())

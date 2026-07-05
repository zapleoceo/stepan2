"""One-off: re-derive funnel STAGE + intent LEAD_TYPE + AUDIENCE for every eligible lead from
its dialog, in a SINGLE broker call per lead. RELABEL ONLY — writes lead.stage (+ a stage_event
audit row), lead.lead_type, lead.audience and nothing else: no replies, no hand-offs, agent_enabled
and the follow-up timer untouched. Never assigns ready/handed_off/manager (a batch must not trigger
a live hand-off) and skips those stages, blocked leads, and the S1 human-handled preserve set
(reclass_exclude_lead).

Ghost guard: a lead that chased the full follow-up schedule (followups_sent >= _GHOST_FOLLOWUPS)
with no reply is forced to `dormant` + lead_type `cold` and the LLM is skipped (mirrors
outbox.py's schedule-exhaustion rule; the stage classifier would otherwise reactivate a ghost).

Two orthogonal axes: lead_type = intent/temperature, audience = who they are (a student can be
hot). Kept in sync with the LEAD TYPE + AUDIENCE blocks in app/modules/conversation/prompt.py.

Run in the container (scripts/ is NOT in the image — docker cp it in first):
  docker cp scripts/reclassify_leads.py stepan2-api:/app/scripts/reclassify_leads.py
  docker exec stepan2-api python -m scripts.reclassify_leads [--branch N] [--limit N] [--dry]

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
log = logging.getLogger("reclassify_leads")

_STAGES = ("new", "nurturing", "qualifying", "presenting", "objection", "dormant")
_EXCLUDED = ("ready", "handed_off", "manager")  # live hand-offs — never set from a batch
_LEAD_TYPES = ("hot", "warm", "cold", "no_budget", "non_target", "unclear")
_AUDIENCES = ("adult", "student")
_GHOST_FOLLOWUPS = 3

_SYSTEM = (
    "Below is a sales chat between a LEAD and the AGENT (Stepan) for IT STEP, a coding school in "
    "Jakarta. From the whole conversation, output THREE independent labels as JSON.\n"
    "\n"
    "1) stage — the funnel stage the lead is in right now (exactly one):\n"
    "- 'new': only just arrived, no substantive back-and-forth yet.\n"
    "- 'nurturing': cold/undecided, being warmed up, no real need surfaced.\n"
    "- 'qualifying': discovery in progress, a concrete need NOT captured yet (default working "
    "stage).\n"
    "- 'presenting': a real need is on the table and the agent is pitching the product to it.\n"
    "- 'objection': the lead is pushing back (price, doubt, timing) and the agent handles it.\n"
    "- 'dormant': the lead EXPLICITLY stopped/refused ('gak jadi','stop','no thanks','nanti aja') "
    "or clearly disengaged. Do NOT infer dormant from a short chat — only an explicit stop.\n"
    "Never output ready/handed_off/manager.\n"
    "\n"
    "2) lead_type — intent/temperature (exactly one; 'unclear' until ~3 messages of signal):\n"
    "- 'hot': explicit intent to enrol/pay/reserve NOW ('cara daftar/mau ikut/gimana bayar').\n"
    "- 'warm': genuine interest, engaged, a real need, no blocker.\n"
    "- 'cold': low intent, vague/one-word, 'cuma lihat/nanya', browsing.\n"
    "- 'no_budget': wants it but can't/won't pay ('gapunya duit', price shock, no income).\n"
    "- 'non_target': wrong audience (asks for something we don't teach), off-topic, trolling, or "
    "explicit 'I don't want it'.\n"
    "- 'unclear': not enough signal yet.\n"
    "\n"
    "3) audience — WHO the lead is, independent of intent (a student can still be hot). Use null "
    "until known:\n"
    "- 'student': school-age / a minor ('masih sekolah','masih SMA/SMP', a teen).\n"
    "- 'adult': a working adult / decision-maker who pays for themselves.\n"
    "\n"
    'Output ONLY this JSON, nothing else:\n'
    '{"stage": "qualifying", "lead_type": "warm", "audience": null}'
)


async def _lead_rows(
    session, branch: int | None, limit: int | None, offset: int = 0,
) -> list[tuple[int, int, str, int, int]]:
    where = [
        "l.stage NOT IN ('ready','handed_off','manager')",
        "l.is_blocked = false",
        "l.id NOT IN (SELECT lead_id FROM reclass_exclude_lead)",
    ]
    params: dict = {}
    if branch:
        where.append("l.branch_id = :b")
        params["b"] = branch
    thr = "(SELECT ct.id FROM channel_thread ct WHERE ct.lead_id = l.id ORDER BY ct.id LIMIT 1)"
    fups = ("(SELECT ct.followups_sent FROM channel_thread ct WHERE ct.lead_id = l.id"
            " ORDER BY ct.id LIMIT 1)")
    q = f"SELECT l.id, l.branch_id, l.stage, {thr}, {fups} FROM lead l WHERE {' AND '.join(where)} ORDER BY l.id"  # noqa: S608, E501
    if limit:
        q += " LIMIT :lim"
        params["lim"] = limit
    if offset:
        q += " OFFSET :off"
        params["off"] = offset
    rows = (await session.execute(text(q), params)).all()
    return [(r[0], r[1], str(r[2]), r[3], int(r[4] or 0)) for r in rows if r[3] is not None]


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


def _parse(raw: str) -> tuple[str | None, str | None, str | None]:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s[3:]
        s = s[4:] if s[:4].lower() == "json" else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return None, None, None
    try:
        d = json.loads(s[i:j + 1])
    except (json.JSONDecodeError, AttributeError):
        return None, None, None
    stage = str(d.get("stage", "")).strip().lower()
    ltype = str(d.get("lead_type", "")).strip().lower()
    aud = str(d.get("audience") or "").strip().lower()
    return (
        stage if stage in _STAGES else None,
        ltype if ltype in _LEAD_TYPES else None,
        aud if aud in _AUDIENCES else None,
    )


async def _apply(session, row, stage, ltype, aud, reason: str, dry: bool) -> str:
    lead_id, branch_id, cur_stage, thread_id, _ = row
    changed = []
    if stage and stage not in _EXCLUDED and stage != cur_stage:
        changed.append(f"{cur_stage}->{stage}")
        if not dry:
            await session.execute(
                text("UPDATE lead SET stage = :s WHERE id = :id"), {"s": stage, "id": lead_id})
            await session.execute(
                text("INSERT INTO stage_event (branch_id, lead_id, thread_id, from_stage,"
                     " to_stage, actor, reason, created_at)"
                     " VALUES (:b,:l,:t,:f,:s,'batch:reclass',:r, now())"),
                {"b": branch_id, "l": lead_id, "t": thread_id, "f": cur_stage, "s": stage,
                 "r": reason})
    if ltype and not dry:
        await session.execute(
            text("UPDATE lead SET lead_type = :v WHERE id = :id"), {"v": ltype, "id": lead_id})
    if aud and not dry:
        await session.execute(
            text("UPDATE lead SET audience = :v WHERE id = :id"), {"v": aud, "id": lead_id})
    if ltype:
        changed.append(f"type={ltype}")
    if aud:
        changed.append(f"aud={aud}")
    return " ".join(changed) if changed else "same"


async def _process(row, llm: BrokerLLM, dry: bool) -> str:
    lead_id, _branch_id, _cur_stage, _thread_id, followups_sent = row
    if followups_sent >= _GHOST_FOLLOWUPS:  # ghosted — dormant + cold, skip the LLM
        async with session_scope() as session:
            return await _apply(session, row, "dormant", "cold", None,
                                "ghosted: followups exhausted (>=3), no reply", dry)
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
                    capability="chat:smart", max_tokens=1500, workflow="reclass",
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
        stage, ltype, aud = _parse(raw)
        if stage is None and ltype is None and aud is None:
            return "error"
        return await _apply(session, row, stage, ltype, aud, "full 2-axis re-check", dry)


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
    log.info("reclassifying %d leads (dry=%s, conc=%d)", len(rows), args.dry, args.concurrency)
    llm = BrokerLLM()
    sem = asyncio.Semaphore(args.concurrency)
    counts: dict[str, int] = {}
    done = 0

    async def run(row, idx: int) -> None:
        nonlocal done
        async with sem:
            r = await _process(row, llm, args.dry)
            bucket = "moved" if "->" in r else ("relabeled" if r != "same" and r not in
                                                ("empty", "error") else r)
            counts[bucket] = counts.get(bucket, 0) + 1
            done += 1
            if args.dry:
                log.info("lead %s: %s", row[0], r)
            elif done % 50 == 0:
                log.info("… %d/%d  %s", done, len(rows),
                         " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    await asyncio.gather(*(run(row, i + 1) for i, row in enumerate(rows)))
    log.info("done: %s", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    asyncio.run(main())

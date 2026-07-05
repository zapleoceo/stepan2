"""Offline bake-off: replay real threads through chat:fast vs chat:smart with the EXACT
prompt the reply engine builds, and measure where fast is safe. Read-only — no budget
records, no lead writes, just broker calls.

Run in the container:  python -m scripts.bakeoff_capability [--branch N] [--per-stage K]

Per current stage it reports: fast JSON-parse rate, and — vs the smart baseline on the same
prompt — agreement on stage / ready / needs_manager / lead_type / reply language. That tells
us which stages fast handles cleanly, i.e. how far the smart_stages boundary can move."""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from app.adapters.db.session import session_scope
from app.adapters.llm.broker import BrokerLLM
from app.modules.conversation.decision import parse_decision
from app.modules.conversation.engine import (
    _ASSISTANT_LAST_NUDGE,
    DecisionEngine,
    _retrieval_query,
)
from app.modules.conversation.needs import needs_summary
from app.modules.conversation.prompt import build_messages, lead_name_hint, source_hint
from app.modules.knowledge.service import KnowledgeService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bakeoff")

_STAGES = ("new", "nurturing", "qualifying", "presenting", "objection", "ready",
           "handed_off", "dormant", "manager")


async def _sample(session, branch: int | None, per_stage: int) -> list[tuple[int, str]]:
    where = "WHERE l.branch_id = :b AND" if branch else "WHERE"
    params: dict = {"b": branch} if branch else {}
    out: list[tuple[int, str]] = []
    for st in _STAGES:
        q = (f"SELECT ct.id FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id "  # noqa: S608
             f"{where} l.stage = :st ORDER BY ct.id DESC LIMIT :k")
        rows = (await session.execute(text(q), {**params, "st": st, "k": per_stage})).all()
        out.extend((r[0], st) for r in rows)
    return out


async def _messages(engine: DecisionEngine, ctx, thread_id: int, lang: str) -> list:
    """Rebuild the exact message list DecisionEngine.complete() sends (without recording spend)."""
    context = await engine.knowledge.knowledge_context(
        ctx.thread.product_slug, query=_retrieval_query(ctx.dialog), thread_id=thread_id)
    notes = await engine.coaching.active_manager_notes()
    messages = build_messages(
        context, ctx.dialog, lang, coaching_notes=notes,
        needs_block=needs_summary(ctx.stored_needs),
        source_block=source_hint(ctx.thread.lead_source),
        name_block=lead_name_hint(ctx.lead.display_name if ctx.lead else None))
    if messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": _ASSISTANT_LAST_NUDGE})
    return messages


async def _one(thread_id: int, stage: str, llm: BrokerLLM) -> dict | None:
    async with session_scope() as session:
        branch_id = (await session.execute(
            text("SELECT l.branch_id FROM channel_thread ct JOIN lead l ON l.id = ct.lead_id "
                 "WHERE ct.id = :t"), {"t": thread_id})).scalar()
        if branch_id is None:
            return None
        engine = DecisionEngine(session, branch_id, llm, KnowledgeService(session, branch_id))
        ctx = await engine.prepare(thread_id, workflow="reply")
        if ctx is None:
            return None
        lang = (ctx.lead.preferred_language if ctx.lead and ctx.lead.preferred_language
                else "id")
        messages = await _messages(engine, ctx, thread_id, lang)

    async def _call(cap: str) -> str | None:
        for attempt in range(3):
            try:
                raw, _ = await llm.chat(messages, capability=cap, require_json_schema=True,
                                        workflow="bakeoff", thread_id=thread_id)
                if raw and raw.strip():
                    return raw
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    log.warning("thread %s %s failed: %s", thread_id, cap, exc)
            await asyncio.sleep(1.5 * (attempt + 1))
        return None

    fast_raw, smart_raw = await asyncio.gather(_call("chat:fast"), _call("chat:smart"))
    try:
        fast = parse_decision(fast_raw) if fast_raw else None
    except ValueError:
        fast = None
    try:
        smart = parse_decision(smart_raw) if smart_raw else None
    except ValueError:
        smart = None
    return {"stage": stage, "fast_ok": fast is not None, "have_both": bool(fast and smart),
            "d_stage": bool(fast and smart and fast.stage == smart.stage),
            "d_ready": bool(fast and smart and fast.ready == smart.ready),
            "d_mgr": bool(fast and smart and fast.needs_manager == smart.needs_manager),
            "d_type": bool(fast and smart and fast.lead_type == smart.lead_type),
            "d_lang": bool(fast and smart and fast.reply_language == smart.reply_language)}


def _agree_pct(rows: list[dict], key: str) -> str:
    both = [r for r in rows if r["have_both"]]
    return f"{round(100 * sum(r[key] for r in both) / len(both))}%" if both else "-"


def _report(rows: list[dict]) -> None:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["stage"], []).append(r)
    log.info("stage         n  fast_ok  =stage  =ready  =mgr  =type  =lang")
    for st in _STAGES:
        rs = by.get(st, [])
        if not rs:
            continue
        ok_pct = round(100 * sum(r["fast_ok"] for r in rs) / len(rs))
        log.info("%-12s %3d   %4d%%   %5s   %5s  %4s  %5s  %5s",
                 st, len(rs), ok_pct, _agree_pct(rs, "d_stage"), _agree_pct(rs, "d_ready"),
                 _agree_pct(rs, "d_mgr"), _agree_pct(rs, "d_type"), _agree_pct(rs, "d_lang"))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", type=int, default=None)
    ap.add_argument("--per-stage", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    async with session_scope() as session:
        sample = await _sample(session, args.branch, args.per_stage)
    log.info("bake-off over %d threads (per_stage=%d)", len(sample), args.per_stage)
    llm = BrokerLLM()
    sem = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = []

    async def run(tid: int, stage: str, idx: int) -> None:
        async with sem:
            r = await _one(tid, stage, llm)
            if r is not None:
                rows.append(r)
            if idx % 20 == 0:
                log.info("… %d/%d", idx, len(sample))

    await asyncio.gather(*(run(t, s, i + 1) for i, (t, s) in enumerate(sample)))
    log.info("DONE — %d usable rows", len(rows))
    _report(rows)


if __name__ == "__main__":
    asyncio.run(main())

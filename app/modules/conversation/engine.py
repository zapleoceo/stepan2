"""DecisionEngine — the shared prepare→LLM→record pipeline behind reply and follow-up.

ReplyService and FollowupService used to duplicate this block (thread/dialog load,
budget guard, RAG context, coaching notes, build_messages, llm.chat, budget record);
now both delegate here and keep only their own semantics on top."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead
from app.modules.budget import BudgetService

from .needs import NeedsProfile, needs_summary, parse_needs
from .prompt import build_messages, lead_name_hint, now_hint, source_hint
from .repository import CoachingNoteRepo, MessageRepo, ThreadRepo

if TYPE_CHECKING:
    from app.modules.knowledge.service import KnowledgeService
    from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

# Defensive: threads_awaiting_reply() only selects threads where the lead spoke last,
# so dialog should always end "in" — but a re-triggered tick (see wiring.try_lock_thread)
# can still land here with the bot's own last message trailing. Mistral hard-rejects that
# shape outright ("Expected last role User or Tool ... but got assistant", code 3230);
# other providers silently treat it as a continuation of that message, which isn't the
# intent either. Nudge a fresh turn instead.
_ASSISTANT_LAST_NUDGE = (
    "[System: no new message from the lead since your last reply. Write a short natural"
    " continuation or check-in if warranted; otherwise keep this turn minimal. Return"
    " the JSON as usual.]"
)


def _retrieval_query(dialog: list, limit: int = 6) -> str:
    """Recent dialog text used as the RAG retrieval query — the index is searched by what
    the conversation is about right now, not the whole (capped) history."""
    return "\n".join(
        (m.text or "").strip() for m in dialog[-limit:] if (m.text or "").strip())


def _fmt_llm_meta(meta: dict) -> str | None:
    model = (meta.get("model") or "").split("/")[-1]
    t_in = meta.get("tokens_in", 0)
    t_out = meta.get("tokens_out", 0)
    cost = meta.get("cost_usd")
    elapsed = meta.get("elapsed_ms")
    req = meta.get("request_id")
    parts: list[str] = []
    if elapsed is not None:  # order: time|id|cost|tokens|model
        parts.append(f"{elapsed / 1000:.1f}s" if elapsed >= 1000 else f"{elapsed}ms")
    if req:
        parts.append(f"#{str(req)[:8]}")
    if cost is not None:
        parts.append("free" if not cost else f"${cost:.4f}")
    if t_in or t_out:
        parts.append(f"{t_in}↑{t_out}↓")
    if model:
        parts.append(model)
    return " | ".join(parts) if parts else None


@dataclass
class DecisionContext:
    """Everything loaded before the LLM call; callers reuse it for their post-processing."""

    thread: Any
    dialog: list
    lead: Lead | None
    stored_needs: NeedsProfile
    budget: BudgetService


class DecisionEngine:
    """Load thread state, guard the budget, run the model, record the spend."""

    def __init__(
        self,
        session: AsyncSession,
        branch_id: int,
        llm: LLMPort,
        knowledge: KnowledgeService,
        broker_budget_s: float | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        # Per-reply generation overrides the model's poll budget (None = the capability default)
        # so a per-thread reply job can wait out a slow broker instead of the old 90s tick cap.
        self._broker_budget_s = broker_budget_s
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)
        self.last_context = ""  # KB context of the most recent complete() — for the guard
        # A DecisionEngine lives for ONE lead-turn (built fresh per decide()/queue_one). Every
        # regen (fast→smart escalation, dedup, guard corrections) calls complete() again with
        # the SAME dialog, so knowledge_context — a broker embed + a full-table vector scan +
        # assembly — was recomputed identically 2-4× per turn. Memoize it per turn so only the
        # first complete() of a turn pays for retrieval; the regens reuse it.
        self._ctx_cache: dict[tuple[str | None, str, bool], str] = {}
        self._tz_offset_h: int | None = None  # branch tz, lazily loaded for the now-hint

    async def _now_block(self) -> str:
        """Branch-local 'today is …' line for the prompt, so the model never offers a past
        session date. tz is loaded once per engine (one lead-turn)."""
        if self._tz_offset_h is None:
            branch = await self.session.get(Branch, self.branch_id)
            self._tz_offset_h = int(branch.tz_offset_h or 0) if branch is not None else 0
        now_local = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=self._tz_offset_h)
        return now_hint(now_local)

    async def prepare(self, thread_id: int, workflow: str) -> DecisionContext | None:
        """None if the thread is foreign, has no dialog, or the branch is over budget."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        dialog = await self.messages.dialog(thread_id, since=thread.context_cleared_at)
        if not dialog:
            return None
        budget = BudgetService(self.session, self.branch_id)
        if await budget.over_budget():
            logger.warning(
                "branch=%d over daily LLM budget — %s skipped", self.branch_id, workflow)
            return None
        lead = await self.session.get(Lead, thread.lead_id)
        stored_needs = parse_needs(lead.needs if lead is not None else None)
        return DecisionContext(thread, dialog, lead, stored_needs, budget)

    async def complete(
        self,
        ctx: DecisionContext,
        thread_id: int,
        *,
        lang: str,
        workflow: str,
        extra_user_msg: str | None = None,
        capability: str = "chat:smart",
        bill: bool = True,
    ) -> tuple[str, dict]:
        """Build the prompt, call the model, record the spend; returns (raw, meta).

        capability picks the model tier (see routing.pick_capability) — default stays smart
        so any caller that doesn't route keeps the strong model."""
        light = workflow == "followup"
        lead_type = ctx.lead.lead_type if ctx.lead is not None else None
        has_open_objection = bool(ctx.stored_needs.objections)
        cache_key = (ctx.thread.product_slug, _retrieval_query(ctx.dialog), light,
                    lead_type, has_open_objection)
        context = self._ctx_cache.get(cache_key)
        if context is None:
            context = await self.knowledge.knowledge_context(
                ctx.thread.product_slug, query=_retrieval_query(ctx.dialog),
                thread_id=thread_id, light=light,
                lead_type=lead_type, has_open_objection=has_open_objection)
            self._ctx_cache[cache_key] = context
        self.last_context = context  # reply-guard checks the draft against exactly this
        notes = await self.coaching.active_manager_notes()
        messages = build_messages(
            context, ctx.dialog, lang, coaching_notes=notes,
            needs_block=needs_summary(ctx.stored_needs),
            source_block=source_hint(ctx.thread.lead_source),
            name_block=lead_name_hint(ctx.lead.display_name if ctx.lead else None),
            manager_note=ctx.lead.manager_note if ctx.lead else None,
            workflow=workflow, now_block=await self._now_block())
        if extra_user_msg is not None:
            messages.append({"role": "user", "content": extra_user_msg})
        elif messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": _ASSISTANT_LAST_NUDGE})
        raw, meta = await self.llm.chat(
            messages, capability=capability, require_json_schema=True,
            workflow=workflow, thread_id=thread_id, branch_id=self.branch_id,
            read_timeout_s=self._broker_budget_s,
        )
        if bill:  # every workflow (sim included) charges its branch's daily LLM ledger
            await ctx.budget.record(float(meta.get("cost_usd") or 0.0))
        return raw, meta

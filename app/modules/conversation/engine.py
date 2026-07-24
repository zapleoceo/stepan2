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

from .dates import annotate_dates
from .needs import NeedsProfile, parse_needs
from .prompt import now_hint
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


# A turn answered without touching the broker (the templated first-contact openers). The
# chat bubble must still say WHY there is no broker line — a blank chip is indistinguishable
# from the meta being lost, which is exactly the regression this marker guards against.
TEMPLATED_META: dict = {"templated": True}
_TEMPLATED_LINE = "templated | free"


def _fmt_llm_meta(meta: dict) -> str | None:
    if meta.get("templated"):
        return _TEMPLATED_LINE
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
    # The branch is over its daily LLM budget. Only set when prepare() was called with
    # allow_over_budget=True — reply.decide() uses it to still ship the zero-cost templated
    # ad-tap opener (no LLM call) while skipping every path that would bill.
    over_budget: bool = False


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
        self.last_context = ""  # KB context of the most recent turn — the money gate's ground
        self._free_ctx: str | None = None  # full KB surface, memoized per turn
        self._tz_offset_h: int | None = None  # branch tz, lazily loaded for the now-hint

    async def _now_local(self) -> datetime:
        """Branch-local now; tz is loaded once per engine (one lead-turn)."""
        if self._tz_offset_h is None:
            branch = await self.session.get(Branch, self.branch_id)
            self._tz_offset_h = int(branch.tz_offset_h or 0) if branch is not None else 0
        return datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=self._tz_offset_h)

    async def _now_block(self) -> str:
        """Branch-local 'today is …' line for the prompt, so the model never offers a past
        session date."""
        return now_hint(await self._now_local())

    async def prepare(
        self, thread_id: int, workflow: str, *, allow_over_budget: bool = False,
    ) -> DecisionContext | None:
        """None if the thread is foreign, has no dialog, or the branch is over budget.

        allow_over_budget=True returns the context anyway with ctx.over_budget set, for the
        one caller (reply.decide) that can answer without spending: the templated ad-tap
        opener costs nothing, and dropping it on over-budget days silenced first contact."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        dialog = await self.messages.dialog(thread_id, since=thread.context_cleared_at)
        if not dialog:
            return None
        budget = BudgetService(self.session, self.branch_id)
        over = await budget.over_budget()
        if over and not allow_over_budget:
            logger.warning(
                "branch=%d over daily LLM budget — %s skipped", self.branch_id, workflow)
            return None
        lead = await self.session.get(Lead, thread.lead_id)
        stored_needs = parse_needs(lead.needs if lead is not None else None)
        return DecisionContext(thread, dialog, lead, stored_needs, budget, over_budget=over)

    async def free_kb_context(self) -> str:
        """The reply prompt's stable prefix: the whole fact surface, date-annotated, memoized
        per turn. Stable within a branch-local day (annotate_dates is the only date-dependent
        input), which is what keeps the broker's prompt cache warm across leads."""
        if self._free_ctx is None:
            context = await self.knowledge.full_knowledge_context()
            self._free_ctx = annotate_dates(context, (await self._now_local()).date())
        self.last_context = self._free_ctx  # the money gate checks the draft against this
        return self._free_ctx

    async def run(
        self, ctx: DecisionContext, messages: list[dict], thread_id: int, *,
        workflow: str, capability: str, bill: bool = True,
    ) -> tuple[str, dict]:
        """Call the model and charge the branch's daily ledger. Prompt-shape agnostic, so v2
        and v3 share one call path (billing, timeouts, broker-log tagging)."""
        raw, meta = await self.llm.chat(
            messages, capability=capability, require_json_schema=True,
            workflow=workflow, thread_id=thread_id, branch_id=self.branch_id,
            read_timeout_s=self._broker_budget_s,
        )
        if bill:  # every workflow (sim included) charges its branch's daily LLM ledger
            await ctx.budget.record(float(meta.get("cost_usd") or 0.0))
        return raw, meta

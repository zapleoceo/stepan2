"""ReplyService — turn a thread's dialog into a Decision, then queue the reply.

LLM stays behind LLMPort (injected, so tests use a fake) and all DB access goes through
BranchScoped repos. No branch_id filtering by hand; no sending here — only enqueue."""
from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, StageEvent
from app.adapters.meta_capi import MetaCapi
from app.domain.enums import Stage
from app.modules.budget import BudgetService
from app.modules.knowledge.service import KnowledgeService
from app.modules.notifications.alerts import AlertService
from app.modules.settings.service import BranchSettings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from .decision import Decision, parse_decision
from .prompt import build_messages
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo

logger = logging.getLogger(__name__)

_BUBBLE_GAP_S = 6  # stagger between split reply bubbles (human typing cadence)
_MAX_BUBBLES = 3


def _split_bubbles(reply: str, max_parts: int = _MAX_BUBBLES) -> list[str]:
    """Split the model's reply on '|||' into ≤max_parts non-empty bubbles; overflow is
    merged into the last one so we never send more than max_parts messages."""
    parts = [p.strip() for p in reply.split("|||") if p.strip()]
    if len(parts) <= max_parts:
        return parts
    return [*parts[: max_parts - 1], " ".join(parts[max_parts - 1:])]


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
    if model:
        parts.append(model)
    if t_in or t_out:
        parts.append(f"{t_in}↑ {t_out}↓")
    if cost is not None:
        parts.append("free" if not cost else f"${cost:.4f}")
    if elapsed is not None:
        parts.append(f"{elapsed / 1000:.1f}s" if elapsed >= 1000 else f"{elapsed}ms")
    if req:
        parts.append(f"id {str(req)[:8]}")
    return " · ".join(parts) if parts else None


class ReplyService:
    """Decide and enqueue the agent's reply for one branch's thread."""

    def __init__(
        self,
        session: AsyncSession,
        branch_id: int,
        llm: LLMPort,
        knowledge: KnowledgeService,
        branch_settings: BranchSettings | None = None,
        notifier: NotifierPort | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.settings = branch_settings
        self._notifier = notifier
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)
        self._last_llm_meta: dict = {}

    async def decide(self, thread_id: int) -> Decision | None:
        """Run the model over the thread; None if the thread is foreign or has no dialog."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        dialog = await self.messages.dialog(thread_id, since=thread.context_cleared_at)
        if not dialog:
            return None

        budget = BudgetService(self.session, self.branch_id)
        if await budget.over_budget():
            logger.warning("branch=%d over daily LLM budget — reply skipped", self.branch_id)
            return None

        context = await self.knowledge.knowledge_context(
            thread.product_slug, query=_retrieval_query(dialog))
        notes = await self.coaching.active_manager_notes()
        lead = await self.session.get(Lead, thread.lead_id)
        messages = build_messages(
            context, dialog, await self._lang(lead), coaching_notes=notes)
        if messages[-1]["role"] == "assistant":
            # Defensive: threads_awaiting_reply() only selects threads where the
            # lead spoke last, so dialog should always end "in" — but a re-triggered
            # tick (see wiring.try_lock_thread) can still land here with the bot's
            # own last message trailing. Mistral hard-rejects that shape outright
            # ("Expected last role User or Tool ... but got assistant", code 3230);
            # other providers silently treat it as a continuation of that message,
            # which isn't the intent either. Nudge a fresh turn instead — same
            # pattern FollowupService already uses for its own assistant-last case.
            messages.append({
                "role": "user",
                "content": "[System: no new message from the lead since your last "
                            "reply. Write a short natural continuation or check-in "
                            "if warranted; otherwise keep this turn minimal. Return "
                            "the JSON as usual.]",
            })
        raw, meta = await self.llm.chat(
            messages, capability="chat:smart", require_json_schema=True,
            workflow="reply", thread_id=thread_id, branch_id=self.branch_id,
        )
        self._last_llm_meta = meta
        await budget.record(float(meta.get("cost_usd") or 0.0))
        return parse_decision(raw)

    async def _lang(self, lead: Lead | None = None) -> str:
        """Reply language ladder: the lead's stated preference wins, else the branch default.
        The KB may be written in any language — this only controls what the bot replies in."""
        if lead is not None and lead.preferred_language:
            return lead.preferred_language
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"

    async def enqueue_reply(self, thread_id: int, decision: Decision) -> Outbox | None:
        """Queue the decided reply bubbles and apply the decision to the lead (S1 semantics)."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        base = self._scheduled_at()
        outbox: Outbox | None = None
        bubbles = list(_split_bubbles(decision.reply))
        last_i = len(bubbles) - 1
        for i, bubble in enumerate(bubbles):
            outbox = await self.outbox.add(
                Outbox(
                    branch_id=self.branch_id,
                    thread_id=thread_id,
                    text=bubble,
                    scheduled_at=base + timedelta(seconds=i * _BUBBLE_GAP_S),
                    llm_info=_fmt_llm_meta(self._last_llm_meta) if i == last_i else None,
                )
            )
        lead = await self.session.get(Lead, thread.lead_id)
        if lead is not None:
            await self._apply_decision(lead, thread, decision)
        if decision.needs_manager:
            await self._raise_manager_alert(
                thread_id, thread.lead_id, decision,
                lead.phone_e164 if lead is not None else None,
            )
        return outbox

    async def _apply_decision(self, lead: Lead, thread, decision: Decision) -> None:
        """Move the funnel: stage priority ready+contact → READY, needs_manager →
        MANAGER, ready w/o contact → PRESENTING, else the model's stage."""
        if decision.product_slug and thread.product_slug is None:
            thread.product_slug = decision.product_slug
            self.session.add(thread)
        if decision.reply_language and decision.reply_language != lead.preferred_language:
            lead.preferred_language = decision.reply_language  # lead switched language — remember
            self.session.add(lead)
        new_stage = self._stage_for(decision, lead)
        if new_stage == lead.stage:
            return
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
            from_stage=str(lead.stage), to_stage=str(new_stage), actor="bot",
            reason="needs_manager" if decision.needs_manager else
                   ("ready" if decision.ready else "model decision"),
        ))
        lead.stage = new_stage
        if new_stage == Stage.MANAGER:
            lead.agent_enabled = False  # human takes over; manager may re-enable
        if new_stage == Stage.READY:
            await self._handoff(lead, thread, decision.ready_subtype)
        self.session.add(lead)
        logger.info("branch=%d lead=%d stage → %s", self.branch_id, lead.id, new_stage)

    def _stage_for(self, decision: Decision, lead: Lead) -> Stage:
        if decision.ready and lead.phone_e164:
            return Stage.READY
        if decision.needs_manager:
            return Stage.MANAGER
        if decision.ready:  # ready without a contact — keep selling until we have one
            return Stage.PRESENTING
        return decision.stage

    async def _handoff(self, lead: Lead, thread, subtype: str | None) -> None:
        """Lead is ready with a contact: bot off, stamp, manager card, CAPI Lead event.

        subtype (deal|openhouse) distinguishes an enrollment from an open-house signup —
        it drives the alert kind and the Meta CAPI event, and feeds the Won-split report."""
        now = datetime.now(UTC).replace(tzinfo=None)
        lead.agent_enabled = False
        lead.handed_off_at = now
        lead.ready_subtype = lead.ready_subtype or subtype or "deal"
        kind = f"ready_{lead.ready_subtype}"
        alerts = AlertService(self.session, self.branch_id, self._notifier)
        try:
            await alerts.raise_alert(
                lead_id=lead.id,
                kind=kind,
                summary_en=f"Lead is ready ({lead.ready_subtype}) · phone {lead.phone_e164}",
                summary_ru=f"Лид готов ({lead.ready_subtype}) · телефон {lead.phone_e164}",
                thread_id=thread.id,
                lead_phone=lead.phone_e164,
            )
        except Exception:
            logger.warning("handoff alert failed lead=%s", lead.id, exc_info=True)
        cfg = self.settings
        if cfg is not None and cfg.meta_pixel_id and cfg.meta_capi_token:
            await MetaCapi().send_lead(
                cfg.meta_pixel_id, cfg.meta_capi_token,
                event_id=f"handoff-{self.branch_id}-{lead.id}",
                phone=lead.phone_e164,
            )

    async def _raise_manager_alert(
        self, thread_id: int, lead_id: int, decision: Decision,
        lead_phone: str | None = None,
    ) -> None:
        q = decision.manager_question or ""
        gap = decision.kb_gap or ""
        summary_en = q or "Lead requests human handoff"
        summary_ru = f"Вопрос: {q}" if q else "Лид запросил менеджера"
        if gap:
            summary_ru += f"\nПробел в KB: {gap}"
        alerts = AlertService(self.session, self.branch_id, self._notifier)
        try:
            await alerts.raise_alert(
                lead_id=lead_id,
                kind="needs_manager",
                summary_en=summary_en,
                summary_ru=summary_ru,
                thread_id=thread_id,
                lead_phone=lead_phone,
            )
        except Exception:
            logger.warning(
                "manager alert failed thread=%s lead=%s", thread_id, lead_id, exc_info=True
            )

    def _scheduled_at(self) -> datetime:
        """Return send time: now + random delay from settings (or immediate if none)."""
        if self.settings is None:
            return datetime.now(UTC).replace(tzinfo=None)
        delay_s = random.randint(  # noqa: S311 — jitter, not crypto
            self.settings.reply_delay_min_s,
            max(self.settings.reply_delay_min_s, self.settings.reply_delay_max_s),
        )
        return (datetime.now(UTC) + timedelta(seconds=delay_s)).replace(tzinfo=None)

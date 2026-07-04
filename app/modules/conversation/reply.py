"""ReplyService — turn a thread's dialog into a Decision, then queue the reply.

LLM stays behind LLMPort (injected, so tests use a fake) and all DB access goes through
BranchScoped repos. No branch_id filtering by hand; no sending here — only enqueue."""
from __future__ import annotations

import logging
import random
import re
from datetime import UTC, datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, StageEvent
from app.adapters.meta_capi import MetaCapi
from app.config import settings
from app.domain.enums import Stage
from app.modules.knowledge.service import KnowledgeService
from app.modules.notifications.alerts import AlertService
from app.modules.settings.service import BranchSettings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from .decision import Decision, parse_decision
from .engine import DecisionEngine, _fmt_llm_meta, _retrieval_query  # noqa: F401 — re-exported
from .needs import merge_needs, parse_needs
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo

logger = logging.getLogger(__name__)

_BUBBLE_GAP_S = settings().bubble_gap_s  # stagger between split reply bubbles
_MAX_BUBBLES = settings().max_bubbles
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")


def _script_lang(text: str) -> str | None:
    """Cyrillic in the lead's own text -> 'ru', independent of the model's self-report.

    decision.reply_language is only set when the model remembers to fill it in - live
    threads showed it drifting back to the branch default (Bahasa) mid-conversation even
    after the lead explicitly switched to Russian, because that self-report was the ONLY
    thing persisting the switch. A lead's own script is a much stronger, cheap signal."""
    return "ru" if _CYRILLIC_RE.search(text or "") else None


def _split_bubbles(reply: str, max_parts: int = _MAX_BUBBLES) -> list[str]:
    """Split the model's reply on '|||' into ≤max_parts non-empty bubbles; overflow is
    merged into the last one so we never send more than max_parts messages."""
    parts = [p.strip() for p in reply.split("|||") if p.strip()]
    if len(parts) <= max_parts:
        return parts
    return [*parts[: max_parts - 1], " ".join(parts[max_parts - 1:])]


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
        engine = DecisionEngine(self.session, self.branch_id, self.llm, self.knowledge)
        ctx = await engine.prepare(thread_id, workflow="reply")
        if ctx is None:
            return None
        lead = ctx.lead
        last_in = next((m for m in reversed(ctx.dialog) if m.direction == "in"), None)
        script_lang = _script_lang(last_in.text if last_in else "")
        lang = script_lang or await self._lang(lead)
        if script_lang and lead is not None and lead.preferred_language != script_lang:
            lead.preferred_language = script_lang  # sticks even if the model forgets to say so
            self.session.add(lead)
        raw, meta = await engine.complete(ctx, thread_id, lang=lang, workflow="reply")
        self._last_llm_meta = meta
        decision = parse_decision(raw)
        if lead is not None:
            merged = merge_needs(ctx.stored_needs, decision.jobs, decision.pains,
                                 decision.gains, decision.discovery_complete)
            lead.needs = merged.to_json()
            self.session.add(lead)
        return decision

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
        meta_line = _fmt_llm_meta(self._last_llm_meta)
        for i, bubble in enumerate(_split_bubbles(decision.reply)):
            outbox = await self.outbox.add(
                Outbox(
                    branch_id=self.branch_id,
                    thread_id=thread_id,
                    text=bubble,
                    scheduled_at=base + timedelta(seconds=i * _BUBBLE_GAP_S),
                    llm_info=meta_line,  # every bubble of the reply shows the same broker line
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
        if decision.hard_stop:
            await self._hard_stop(lead, thread)
            return
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

    async def _hard_stop(self, lead: Lead, thread) -> None:
        """Lead explicitly demanded we stop: let the one queued apology go out, then silence
        the account — bot off, dormant, follow-up timer cleared. Anti-ban: a nudge after an
        explicit stop turns an annoyed lead into a spam report against the IG account."""
        thread.next_followup_at = None
        self.session.add(thread)
        if lead.stage != Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
                actor="bot", reason="hard_stop",
            ))
            lead.stage = Stage.DORMANT
        lead.agent_enabled = False
        self.session.add(lead)
        logger.info("branch=%d lead=%d hard-stop → dormant, bot off", self.branch_id, lead.id)

    def _stage_for(self, decision: Decision, lead: Lead) -> Stage:
        if decision.ready and lead.phone_e164:
            return Stage.READY
        if decision.needs_manager:
            return Stage.MANAGER
        if decision.ready:  # ready without a contact — keep selling until we have one
            return Stage.PRESENTING
        # Discovery gate: never present until we've captured a real need (a pain or gain),
        # even when the lead opened with a direct product question. Force one more
        # discovery turn instead — the code backstop behind the prompt's discover-first rule.
        if decision.stage in (Stage.PRESENTING, Stage.OBJECTION) and not self._needs_captured(
            decision, lead
        ):
            return Stage.QUALIFYING
        return decision.stage

    @staticmethod
    def _needs_captured(decision: Decision, lead: Lead) -> bool:
        if decision.discovery_complete or decision.has_needs():
            return True
        stored = parse_needs(lead.needs)
        return stored.discovery_complete or stored.has_needs()

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

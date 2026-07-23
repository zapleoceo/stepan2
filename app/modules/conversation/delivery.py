"""ReplyService — turn a thread's dialog into a Decision, then queue the reply.

LLM stays behind LLMPort (injected, so tests use a fake) and all DB access goes through
BranchScoped repos. No branch_id filtering by hand; no sending here — only enqueue."""
from __future__ import annotations

import logging
import random
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, Product, StageEvent, ThreadLog
from app.adapters.meta_capi import MetaCapi
from app.config import settings
from app.domain.enums import HUMAN_LED_STAGES, Stage
from app.modules.knowledge.service import KnowledgeService
from app.modules.leads.phone import to_e164
from app.modules.notifications.alerts import AlertService
from app.modules.settings.service import BranchSettings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from . import guard
from .decision import Decision
from .engine import DecisionEngine, _fmt_llm_meta, _retrieval_query  # noqa: F401 — re-exported
from .money_gate import MONEY_ESCALATION_REASON
from .needs import parse_needs
from .repository import CoachingNoteRepo, MessageRepo, OutboxRepo, ThreadRepo
from .signals import DISCOVERY_TURN_CAP as _DISCOVERY_TURN_CAP
from .signals import SOFT_NO_RE as _SOFT_NO_RE
from .signals import postpone_days as _postpone_days

logger = logging.getLogger(__name__)

# Reasons the SYSTEM produced, not the model — kept out of the chat chronology.
TECHNICAL_HANDOFF_REASONS = (MONEY_ESCALATION_REASON, guard.GUARD_HANDOFF_REASON)

_BUBBLE_GAP_S = settings().bubble_gap_s  # stagger between split reply bubbles
_MAX_BUBBLES = settings().max_bubbles
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
# Indonesian mobile number as typed in chat: 08…, 628…, +628…, with optional spaces/dashes.
_ID_PHONE_RE = re.compile(r"(?:\+?62[\s\-]?|0)8\d(?:[\s\-]?\d){6,10}")
# A needs_manager turn always mutes the bot (agent_enabled=False) — but the model's own
# reply that same turn is about answering the lead's question, not about announcing a
# hand-off; nothing told the LEAD a human is now taking over. A lead who then sends a
# follow-up (e.g. a phone number, thread 1023) gets pure silence — no bot, no confirmation,
# because the account is already muted. Append this deterministic closing line whenever the
# stage newly flips to MANAGER, so the lead is never left hanging without an explanation.
_MANAGER_HANDOFF_CLOSING = (
    "Terima kasih ya Kak! Untuk ini tim kami yang akan bantu langsung - nanti dihubungi via "
    "telepon atau WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) ya 🙏"
)
# The sale/READY exit muted the bot like MANAGER but never guaranteed the lead a closing —
# it relied on the model's own reply confirming next steps, which isn't guaranteed. Append
# this on the fresh READY flip so a won lead always knows what happens next (same shape as
# the manager closing, but about the enrollment: registration → payment during work hours).
_READY_HANDOFF_CLOSING = (
    "Siap Kak! Pendaftaran Kakak aku teruskan ke tim ya - nanti dihubungi via telepon atau "
    "WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) untuk langkah pembayaran & jadwal 🙏"
)

def _script_lang(text: str) -> str | None:
    """Cyrillic in the lead's own text -> 'ru', independent of the model's self-report.

    decision.reply_language is only set when the model remembers to fill it in - live
    threads showed it drifting back to the branch default (Bahasa) mid-conversation even
    after the lead explicitly switched to Russian, because that self-report was the ONLY
    thing persisting the switch. A lead's own script is a much stronger, cheap signal."""
    return "ru" if _CYRILLIC_RE.search(text or "") else None


# Horizontal-rule / lone-heading lines the model sometimes leaks into a DM (markdown bleed).
_MD_ARTIFACT_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,}|—{3,}|#{1,6})[ \t]*$", re.MULTILINE)


def _clean_bubble(text: str) -> str:
    """Strip markdown artifacts that read as noise in a chat bubble — a horizontal rule
    (---, ***, ___) or a lone heading marker (live thread 2778: a trailing '---' shipped to
    the lead). Conservative: only removes lines that are ONLY the artifact, never trims real
    content (so 'Rp 500.000 - 600.000' is untouched)."""
    cleaned = _MD_ARTIFACT_RE.sub("", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return guard.normalize_address(cleaned.strip())


def _split_bubbles(reply: str, max_parts: int = _MAX_BUBBLES) -> list[str]:
    """Split the model's reply on '|||' into ≤max_parts non-empty bubbles; overflow is
    merged into the last one so we never send more than max_parts messages."""
    parts = [c for p in reply.split("|||") if (c := _clean_bubble(p))]
    if len(parts) <= max_parts:
        return parts
    return [*parts[: max_parts - 1], " ".join(parts[max_parts - 1:])]


def _reply_bubble_cap(reply: str) -> int:
    """At most TWO messages in a row for a normal reply — three DMs with no lead turn between
    reads as a monolog and raises spam-detection risk (20-chat audit: bot share ~65-70%). The
    numbered-menu turns (ad opener, clarify/goal menu) genuinely need their 3rd bubble for the
    options, so those keep the full _MAX_BUBBLES; everything else is capped at 2."""
    return _MAX_BUBBLES if "1️⃣" in (reply or "") else 2


async def guard_prompt(session: AsyncSession, branch_id: int) -> str | None:
    """The reply-guard checker prompt, editable per branch via the `guard_verify` KB
    doc (resolved through a shared-KB link); None → guard.py's built-in default."""
    from app.modules.knowledge.repository import KnowledgeRepo  # noqa: PLC0415
    from app.modules.knowledge.source import effective_kb_branch  # noqa: PLC0415
    kb = await effective_kb_branch(session, branch_id)
    doc = await KnowledgeRepo(session, kb).by_slug("guard_verify")
    return doc.content if doc and (doc.content or "").strip() else None


async def raise_manager_alert(
    session: AsyncSession, branch_id: int, notifier: NotifierPort | None, llm: LLMPort,
    thread_id: int, lead_id: int, decision: Decision, lead_phone: str | None = None,
) -> None:
    """Notify a human that this thread needs one — the KB genuinely has no answer, so a
    manager works it and (per policy) feeds the missing fact back into the KB afterward.
    Shared by the live-reply path AND follow-up nudges: a needs_manager decision means the
    same thing regardless of which path produced it, so both must actually alert (a nudge
    that silently sets needs_manager with no alert was the pre-2026-07-07 followup gap)."""
    q = decision.manager_question or ""
    gap = decision.kb_gap or ""
    summary_en = q or gap or "Thread handed to a human"
    if q:
        summary_ru = f"Вопрос: {q}"
        if gap:
            summary_ru += f"\nПробел в KB: {gap}"
    elif gap:
        summary_ru = gap  # a guard-forced or model-named reason — never claim the lead asked
    else:
        summary_ru = "Лид запросил менеджера"
    alerts = AlertService(session, branch_id, notifier, llm=llm)
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


class ReplyDelivery:
    """Everything between a decision and the lead seeing it.

    Deliberately knows nothing about how the reply was produced: it enqueues bubbles, applies
    the decision to the lead (stage, segment, phone, product), raises hand-off alerts and drives
    the outbox. ReplyService adds the decision procedure on top."""

    def __init__(
        self,
        session: AsyncSession,
        branch_id: int,
        llm: LLMPort,
        knowledge: KnowledgeService,
        branch_settings: BranchSettings | None = None,
        notifier: NotifierPort | None = None,
        broker_budget_s: float | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.settings = branch_settings
        self._notifier = notifier
        self._broker_budget_s = broker_budget_s  # per-reply broker poll budget (None = default)
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)
        self.coaching = CoachingNoteRepo(session, branch_id)
        self._last_llm_meta: dict = {}

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
        # Idempotency backstop: if a sibling run already answered this exact inbound while
        # THIS run was slow (a guard regen against a broker near its own timeout ceiling), the
        # watermark has already caught up — don't queue a second reply. This is the last line
        # of defense behind the advisory lock: a job ARQ killed for exceeding worker_job_timeout_s
        # can keep running as a zombie coroutine past that point (asyncio cancellation doesn't
        # always land mid-await), so the lock alone doesn't fully close the gap (2026-07-07).
        if thread.last_out_at is not None and thread.last_in_at is not None \
                and thread.last_out_at >= thread.last_in_at:
            logger.warning(
                "enqueue_reply: branch=%d thread=%d already answered by a sibling run — "
                "dropping the duplicate", self.branch_id, thread_id)
            return None
        base = self._scheduled_at()
        outbox: Outbox | None = None
        meta_line = _fmt_llm_meta(self._last_llm_meta)
        bubbles = _split_bubbles(decision.reply, max_parts=_reply_bubble_cap(decision.reply))
        for i, bubble in enumerate(bubbles):
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
        exit_kind: str | None = None
        if lead is not None:
            exit_kind = await self._apply_decision(lead, thread, decision)
        if exit_kind is not None:
            # The lead just exited the funnel (manager hand-off or a won deal) — say what
            # happens next instead of going silent (thread 1023: a lead who sent a follow-up
            # phone number 2 days after a needs_manager mute got zero acknowledgment). READY
            # relied on the model's reply confirming next steps; now it's guaranteed too.
            closing = (_MANAGER_HANDOFF_CLOSING if exit_kind == "manager"
                       else _READY_HANDOFF_CLOSING)
            outbox = await self.outbox.add(
                Outbox(
                    branch_id=self.branch_id,
                    thread_id=thread_id,
                    text=closing,
                    scheduled_at=base + timedelta(seconds=len(bubbles) * _BUBBLE_GAP_S),
                    llm_info=meta_line,
                )
            )
        is_openhouse_rsvp = decision.ready and decision.ready_subtype == "openhouse"
        if decision.needs_manager and not is_openhouse_rsvp:
            await self._raise_manager_alert(
                thread_id, thread.lead_id, decision,
                lead.phone_e164 if lead is not None else None,
            )
        return outbox

    def _sync_lead_fields(self, lead: Lead, thread, decision: Decision) -> None:
        """Copy this turn's observations onto the lead/thread — product, language, segment,
        and a freshly-typed phone. Pure field sync, no funnel-stage logic (see _apply_decision
        for that) — split out so each responsibility can be read and tested on its own."""
        # The model may re-qualify a product it inferred on an earlier turn ('model') or one
        # that was never anchored ('None'), but never overrides an ad-matched product ('ad')
        # or a manager's manual pick ('manager') — thread 4943: an ad-mapped SMM lead had its
        # product silently swapped to Vibe Coding by the model's own re-qualification, and the
        # wrong product's (real, grounded) price got quoted when the lead asked directly. An
        # ad click is stronger evidence of intent than the model's read of the conversation, so
        # it must not be overwritten without a human correction.
        if (
            decision.product_slug
            and decision.product_slug != thread.product_slug
            and thread.product_source in (None, "model")
        ):
            # Logged with actor="agent" (renders as "Степан" — see who.agent in _i18n.py) so
            # the chat timeline can never again read as a manager having clicked the product
            # dropdown when it was really the model re-qualifying — the manual endpoint
            # (_routes_chat.chat_product) is the only other writer of this log kind, and it
            # always attributes to the signed-in session's name.
            self.session.add(ThreadLog(
                branch_id=self.branch_id, thread_id=thread.id, kind="product_changed",
                detail=f"{thread.product_slug or '∅'} → {decision.product_slug or '∅'}",
                actor="agent",
            ))
            thread.product_slug = decision.product_slug
            thread.product_source = "model"
            self.session.add(thread)
        if decision.reply_language and decision.reply_language != lead.preferred_language:
            lead.preferred_language = decision.reply_language  # lead switched language — remember
            self.session.add(lead)
        if decision.lead_type and decision.lead_type != lead.lead_type:
            lead.lead_type = decision.lead_type  # intent segment — for routing + reporting
            self.session.add(lead)
        if decision.audience and decision.audience != lead.audience:
            lead.audience = decision.audience  # who they are (adult/student) — reporting + path
            self.session.add(lead)
        # Capture a phone the lead typed in-chat (channel metadata rarely carries one). This
        # must land BEFORE _stage_for so the same turn the lead sends their number can pass the
        # hand-off gate — a manager can't work a deal without a contact.
        if decision.phone and not lead.phone_e164:
            cc = self.settings.phone_country_code if self.settings else "62"
            normalized = to_e164(decision.phone, cc)
            if normalized:
                lead.phone_e164 = normalized
                self.session.add(lead)

    async def _apply_decision(self, lead: Lead, thread, decision: Decision) -> str | None:
        """Move the funnel: stage priority ready+contact → READY, needs_manager →
        MANAGER, ready w/o contact → PRESENTING, else the model's stage. An openhouse RSVP
        is a side-channel notification, not a stage transition — see _stage_for.

        Returns "manager" / "ready" when the stage just flipped to that exit this turn (a
        fresh mute, not an already-exited lead) so the caller appends the matching closing
        line; None otherwise."""
        was_non_target = lead.lead_type == "non_target"
        self._sync_lead_fields(lead, thread, decision)
        if decision.hard_stop:
            await self._hard_stop(lead, thread)
            return None
        # Non-target terminal state: a lead already classified non_target on an EARLIER turn
        # and STILL off-topic now (the same condition that fed _NON_TARGET_NUDGE) has had its
        # one polite closing line — wind it down to DORMANT so a wrong-audience lead doesn't
        # linger in the active list burning a reply every inbound. A fresh inbound with real
        # interest still revives it (ingest._revive_bot), and the model can re-classify it.
        if was_non_target and lead.lead_type == "non_target" \
                and lead.stage not in HUMAN_LED_STAGES:
            await self._soft_close_dormant(lead, thread)
            return None
        # The bound product's kind decides deal-vs-RSVP: an event product is ALWAYS an
        # openhouse-style RSVP (notify team, bot stays on), regardless of what the model
        # guessed — the model only picks the subtype for non-event products.
        ready = self._is_ready(decision)
        eff_subtype = decision.ready_subtype
        if ready and thread.product_slug \
                and await self._product_kind(thread.product_slug) == "event":
            eff_subtype = "openhouse"
        if ready and eff_subtype == "openhouse":
            await self._handoff_openhouse(lead, thread)
        inbound = await self.messages.inbound_count(thread.id)
        soft_no = await self._snooze_on_soft_no(lead, thread)
        new_stage = self._stage_for(decision, lead, inbound, eff_subtype, soft_no=soft_no)
        if new_stage == lead.stage:
            return None
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
            from_stage=str(lead.stage), to_stage=str(new_stage), actor="bot",
            reason="needs_manager" if decision.needs_manager else
                   ("ready" if ready else "model decision"),
        ))
        # Mirrors the manual stage-move reason popup, but for the bot's OWN decision — visible
        # in the same chat chronology so a manager can see WHY Stepan moved the funnel, not
        # just that it did. A forced MANAGER override needs its OWN reason, not the model's
        # stage_reason verbatim: that field describes the stage the model ITSELF asked for
        # (e.g. 'presenting'), which needs_manager then overrides to MANAGER regardless —
        # logging it as-is reads as a mismatch ("лид... — переход в presenting" next to a
        # presenting→manager row). kb_gap/manager_question are what actually explain the
        # escalation; stage_reason is only the right source for a non-escalation move. Model
        # non-compliance (the field left null despite being "required") also gets a fallback
        # here rather than a silent gap in the chronology.
        if new_stage == Stage.MANAGER and decision.needs_manager:
            # stage_reason is deliberately NOT in this chain — it describes the stage the
            # model asked for (e.g. presenting), which reads as a mismatch next to a MANAGER
            # row. A guard-forced hand-off stamps kb_gap, so this fallback is a last resort.
            reason_text = (
                decision.kb_gap or decision.manager_question
                or "эскалация на менеджера без указанной причины"
            )
        else:
            reason_text = decision.stage_reason
        # A machine-forced hand-off's reason is diagnostic detail for the Telegram alert
        # (raise_manager_alert, unaffected by this) — showing it in the CHAT chronology too is
        # just clutter a human keeps having to delete by hand (asked three times in one session,
        # 2026-07-22). A genuine model-named reason is real context worth keeping visible; the
        # technical one is not.
        is_technical_handoff = reason_text is not None and reason_text.startswith(
            TECHNICAL_HANDOFF_REASONS)
        if reason_text and not is_technical_handoff:
            self.session.add(ThreadLog(
                branch_id=self.branch_id, thread_id=thread.id,
                kind="stage_reason", detail=reason_text, actor="bot",
            ))
        lead.stage = new_stage
        if new_stage == Stage.MANAGER:
            lead.agent_enabled = False  # human takes over; manager may re-enable
        if new_stage == Stage.READY:
            await self._handoff(lead, thread, eff_subtype)
        self.session.add(lead)
        logger.info("branch=%d lead=%d stage → %s", self.branch_id, lead.id, new_stage)
        if new_stage == Stage.MANAGER:
            return "manager"
        if new_stage == Stage.READY:
            return "ready"
        return None

    async def _snooze_on_soft_no(self, lead: Lead, thread) -> bool:  # noqa: ANN001
        """A polite 'not now' gets ONE dated re-contact — never a kill, never a nudge storm.

        Audit of threads >=2000: the model may set DORMANT itself, and did so on 7 leads whose
        last word was a soft no ("Nggak kak, makasih. Next time aja ya", "Nanti saya fikirkan
        lagi", "Skip dulu, harganya gak masuk") — dead on the spot, zero follow-ups. The
        funnel guards the opposite direction (only code may set READY) but left DORMANT wide
        open, so the bot could kill a warm lead unilaterally.

        The naive fix — forbid DORMANT — is worse: the lead then enters the normal 1/4/24/120h
        cycle and gets FOUR nudges after saying no, which is the ban vector that produced
        "Gak usah ganggu aku lagi" (threads 2045/1996). So collapse the remaining schedule to
        its LAST step instead: exactly one gentle re-contact (~5 days out), then dormant. That
        is the KB's own rule — "a vague 'later' is a lost lead; a dated 'later' is a plan".

        Returns True when the lead soft-no'd this turn (the stage gate then blocks DORMANT).
        """
        last_in = await self.messages.last_inbound_text(thread.id)
        if not last_in or not _SOFT_NO_RE.search(last_in):
            return False
        if guard.lead_signaled_annoyance(last_in):
            return False  # a real "stop bothering me" — the hard-stop path owns it, not a snooze
        if lead.stage in HUMAN_LED_STAGES or lead.lead_type == "non_target":
            return False
        # The lead may have NAMED their own re-contact time ("bulan depan", "abis gajian",
        # "2 minggu lagi") — a dated 'later' beats any fixed snooze: schedule the single
        # re-contact exactly when THEY said (owner idea 2026-07-19). The OutboxSender's
        # step-arming is bypassed by setting next_followup_at directly past the schedule.
        named_days = _postpone_days(last_in)
        schedule = self.settings.followup_schedule_h if self.settings else []
        if named_days is not None:
            from datetime import UTC, datetime, timedelta  # noqa: PLC0415
            thread.followups_sent = max(
                thread.followups_sent, len(schedule) - 1 if schedule else 0)
            thread.next_followup_at = datetime.now(UTC).replace(tzinfo=None) \
                + timedelta(days=named_days)
            self.session.add(thread)
            logger.info(
                "branch=%d thread=%d soft-no with NAMED time → re-contact in %dd",
                self.branch_id, thread.id, named_days)
        elif schedule and thread.followups_sent < len(schedule) - 1:
            thread.followups_sent = len(schedule) - 1  # only the final, longest step remains
            self.session.add(thread)
            logger.info(
                "branch=%d thread=%d soft-no → snoozed to one final nudge (+%dh)",
                self.branch_id, thread.id, schedule[-1])
        return True

    async def _product_kind(self, slug: str) -> str:
        row = (await self.session.execute(
            select(Product.kind).where(
                Product.branch_id == self.branch_id, Product.slug == slug)
        )).first()
        return row[0] if row else "course"

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

    async def _soft_close_dormant(self, lead: Lead, thread) -> None:
        """A repeatedly-off-topic non_target lead: the model's polite closing line is already
        queued this turn — now wind the funnel down to DORMANT (bot off, follow-up timer
        cleared) so a wrong-audience lead stops occupying the active queue. Softer than
        _hard_stop (no explicit stop demand); a fresh inbound with real interest revives it.

        A human gets ONE ping about the close — no phone required (the usual phone gate is
        for deals; a troll won't give a number and shouldn't be asked for one). 2026-07
        audit: money-beggars and spammers (threads 4237/4113/2707) kept getting pitches for
        days because nobody ever saw them; the owner decided: alert + go silent."""
        thread.next_followup_at = None
        self.session.add(thread)
        if lead.stage != Stage.DORMANT:
            self.session.add(StageEvent(
                branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
                from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
                actor="bot", reason="non_target",
            ))
            lead.stage = Stage.DORMANT
            try:
                await AlertService(
                    self.session, self.branch_id, self._notifier, llm=self.llm,
                ).raise_alert(
                    lead_id=lead.id, kind="non_target",
                    summary_en="Non-target lead closed — bot went silent",
                    summary_ru="Нецелевой лид: бот закрыл диалог и замолчал. "
                               "Гляньте на всякий случай — вдруг это живой клиент.",
                    thread_id=thread.id, lead_phone=lead.phone_e164)
            except Exception:
                logger.warning("non_target close alert failed lead=%s", lead.id, exc_info=True)
        lead.agent_enabled = False
        self.session.add(lead)
        logger.info("branch=%d lead=%d non_target → dormant, bot off", self.branch_id, lead.id)

    @staticmethod
    def _is_ready(decision: Decision) -> bool:
        """Readiness is either the `ready` flag OR the model putting stage='ready' directly.
        Both must go through the same phone gate — otherwise a model that writes stage='ready'
        hands a lead to a manager with no contact (the exact defect on lead 1561)."""
        return decision.ready or decision.stage == Stage.READY

    def _stage_for(self, decision: Decision, lead: Lead, inbound_count: int = 0,
                   ready_subtype: str | None = None, soft_no: bool = False) -> Stage:
        # Once a lead is in a HUMAN-LED stage (manager took it over, or it's already ready/
        # handed off), only a manual UI action may move it out — the bot never auto-moves the
        # funnel stage again, even if it keeps talking (agent_enabled can stay on; see
        # HUMAN_LED_STAGES). Live bug (thread 2274): a manager moved a lead to MANAGER, the
        # bot's very next decision moved it straight back to qualifying on its own read of
        # the conversation — the manager's call was silently overridden.
        if lead.stage in HUMAN_LED_STAGES:
            return lead.stage
        ready = self._is_ready(decision)
        if ready and ready_subtype == "openhouse":
            # An event RSVP is a notify-only side channel (see _handoff_openhouse) — it's
            # a sale (of a seat, not a course) but NOT a hand-off: the bot keeps talking,
            # so never let it force READY/MANAGER and silence the account. A model that wrote
            # stage='ready' directly would otherwise slip READY through here (→ _handoff mutes
            # the bot), so remap it down to PRESENTING — the same defensive depth as _is_ready.
            return Stage.PRESENTING if decision.stage == Stage.READY else decision.stage
        if ready and lead.phone_e164:
            return Stage.READY
        if decision.needs_manager:
            return Stage.MANAGER
        if ready:  # ready without a contact — keep selling / ask for the phone, don't hand off
            return Stage.PRESENTING
        # Discovery gate: don't present until a real need (pain + gain) is captured — the
        # code backstop behind the prompt's discover-first rule. BUT it's an EARLY gate, not
        # an infinite interrogation: once the lead has taken _DISCOVERY_TURN_CAP turns, stop
        # forcing discovery (a non-yielding lead is better served by a value pitch than a
        # sixth question) and trust the model's PRESENTING.
        if (
            decision.stage in (Stage.PRESENTING, Stage.OBJECTION)
            and inbound_count < _DISCOVERY_TURN_CAP
            and not self._needs_captured(decision, lead)
        ):
            return Stage.QUALIFYING
        # A polite "not now" is an OBJECTION to work later, not a corpse. The model reached
        # for DORMANT on 7 such leads (audit of threads >=2000) — killing them the moment they
        # hesitated. _snooze_on_soft_no has already collapsed the cycle to one final nudge, so
        # this costs one gentle re-contact, not a nudge storm.
        if soft_no and decision.stage == Stage.DORMANT:
            return Stage.OBJECTION
        return decision.stage

    @staticmethod
    def _needs_captured(decision: Decision, lead: Lead) -> bool:
        # discovery_complete only counts when backed by a captured PAIN — the model sets the
        # flag prematurely with pains=[] (thread 1081), which skips warm-up and leaves needs
        # uncollected. Require the pain here so the bot keeps discovering until it has one.
        this_turn = decision.has_needs() or (decision.discovery_complete and bool(decision.pains))
        return this_turn or parse_needs(lead.needs).captured()

    async def _handoff(self, lead: Lead, thread, subtype: str | None) -> None:
        """Lead is ready with a contact: bot off, stamp, manager card, CAPI Lead event.

        subtype (deal|openhouse) distinguishes an enrollment from an open-house signup —
        it drives the alert kind and the Meta CAPI event, and feeds the Won-split report."""
        now = datetime.now(UTC).replace(tzinfo=None)
        lead.agent_enabled = False
        lead.handed_off_at = now
        # A genuine (non-openhouse) hand-off must not inherit a stale 'openhouse' marker
        # left by an earlier RSVP — the enrollment is the real outcome, so drop the stale
        # marker and re-derive the deal subtype (else the deal reports as ready_openhouse).
        if lead.ready_subtype == "openhouse" and subtype != "openhouse":
            lead.ready_subtype = None
        lead.ready_subtype = lead.ready_subtype or subtype or "deal"
        kind = f"ready_{lead.ready_subtype}"
        alerts = AlertService(self.session, self.branch_id, self._notifier, llm=self.llm)
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

    async def _handoff_openhouse(self, lead: Lead, thread) -> None:
        """Lead RSVP'd to an event (open house / demo day): notify the team with a
        callback-hours note, keep the bot ON. Unlike _handoff (a course deal), this is
        a seat sale, not a hand-off — no agent_enabled/stage change, no CAPI event.

        Notify only once we have a PHONE — per policy, no team ping for a contact-less RSVP:
        the bot keeps talking and asks for the WhatsApp first, and the ping fires the turn a
        number is in hand. Fires at most ONCE (ready_subtype='openhouse' is the 'already
        notified' marker, set ONLY when the alert actually goes out, so a phone-less RSVP can
        still ping later once the number arrives)."""
        if lead.ready_subtype == "openhouse":  # already notified
            return
        if not lead.phone_e164:
            # No phone yet — don't ping the team for a contact-less RSVP; keep the bot on to
            # collect the WhatsApp first. A later turn WITH a number fires the ping.
            return
        lead.ready_subtype = "openhouse"  # mark notified only now that the alert actually fires
        alerts = AlertService(self.session, self.branch_id, self._notifier, llm=self.llm)
        try:
            await alerts.raise_alert(
                lead_id=lead.id,
                kind="ready_openhouse",
                summary_en=(
                    f"Lead RSVP'd for an event · phone {lead.phone_e164} · IT STEP will call "
                    "back Mon-Fri, 09:00-18:00 WIB (no same-day callback outside those hours)"
                ),
                summary_ru=(
                    f"Лид согласился на ивент · телефон {lead.phone_e164} · перезвонят в "
                    "рабочее время IT STEP (Пн-Пт, 09:00-18:00 WIB, без обещания в тот же день)"
                ),
                thread_id=thread.id,
                lead_phone=lead.phone_e164,
            )
        except Exception:
            logger.warning("openhouse alert failed lead=%s", lead.id, exc_info=True)
        self.session.add(lead)

    async def _raise_manager_alert(
        self, thread_id: int, lead_id: int, decision: Decision,
        lead_phone: str | None = None,
    ) -> None:
        await raise_manager_alert(
            self.session, self.branch_id, self._notifier, self.llm,
            thread_id, lead_id, decision, lead_phone)

    def _scheduled_at(self) -> datetime:
        """Return send time: now + random delay from settings (or immediate if none)."""
        if self.settings is None:
            return datetime.now(UTC).replace(tzinfo=None)
        delay_s = random.randint(  # noqa: S311 — jitter, not crypto
            self.settings.reply_delay_min_s,
            max(self.settings.reply_delay_min_s, self.settings.reply_delay_max_s),
        )
        return (datetime.now(UTC) + timedelta(seconds=delay_s)).replace(tzinfo=None)

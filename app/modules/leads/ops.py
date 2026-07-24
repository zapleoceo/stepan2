"""Lead funnel operations driven by an external system through the MCP connector.

find_lead   → locate a lead by phone (the cross-channel key)
move_lead   → set the funnel stage + journal a StageEvent
close_deal  → deal won: hand off, stop the bot
call_failed → couldn't reach by phone: journal it, re-arm the bot, and have
              Stepan write the lead to continue in chat

Transport-agnostic: the stdio MCP server calls the HTTP wrapper, which calls these.
No LLM work happens inside a stage move — only call_failed generates a message.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, Outbox, StageEvent
from app.domain.enums import BOT_SILENT_STAGES, HUMAN_LED_STAGES, Stage

logger = logging.getLogger(__name__)

_CALL_FAILED_NUDGE = (
    "[System: a teammate just tried to reach this lead by phone and could not get"
    " through. Write ONE short, warm message in {lang} that mentions you tried to"
    " call, and offer to help right here in chat / answer whatever they need. Sound"
    " human, don't over-apologise, don't repeat their name. Return the JSON as usual.]"
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _match_key(phone: str) -> str:
    """Country/format-agnostic lookup key: the trailing national digits. Stored phones are
    canonical '+<cc>…' E.164 (the only writers are phone.to_e164 / phone.extract_phone), but
    a find_lead query may arrive as '0812…', '62…' or '+62…' — comparing the last 9 significant
    digits matches all of those within a branch without re-hardcoding a country prefix here."""
    digits = "".join(c for c in phone if c.isdigit())
    return digits[-9:] if len(digits) >= 9 else digits


@dataclass
class LeadOpResult:
    ok: bool
    detail: str
    lead_id: int | None = None
    name: str | None = None
    phone: str | None = None
    stage: str | None = None
    from_stage: str | None = None
    message_queued: bool = False
    candidates: list[dict] = field(default_factory=list)


async def find_lead(
    session: AsyncSession, phone: str, branch_id: int | None = None,
) -> Lead | None:
    """Match a lead by phone across channels. Phone is the cross-channel identity key;
    branch_id narrows the search when the caller knows it, else searches every branch."""
    norm = _match_key(phone)
    if not norm:
        return None
    stmt = select(Lead).where(Lead.phone_e164.is_not(None))
    if branch_id is not None:
        stmt = stmt.where(Lead.branch_id == branch_id)
    leads = (await session.execute(stmt)).scalars().all()
    for lead in leads:  # match BOTH sides on the national number — formats/country prefixes vary
        if _match_key(lead.phone_e164 or "") == norm:
            return lead
    return None


async def _journal(
    session: AsyncSession, lead: Lead, to_stage: Stage, reason: str,
) -> None:
    session.add(StageEvent(
        branch_id=lead.branch_id, lead_id=lead.id, thread_id=None,
        from_stage=str(lead.stage), to_stage=str(to_stage), actor="mcp", reason=reason,
    ))


def _result(lead: Lead, from_stage: str, detail: str, queued: bool = False) -> LeadOpResult:
    return LeadOpResult(
        ok=True, detail=detail, lead_id=lead.id, name=lead.display_name,
        phone=lead.phone_e164, stage=str(lead.stage), from_stage=from_stage,
        message_queued=queued,
    )


async def move_lead(
    session: AsyncSession, lead: Lead, stage: str, note: str | None = None,
) -> LeadOpResult:
    """Set the funnel stage explicitly. MANAGER silences the bot (human takeover);
    any active messaging stage re-arms it."""
    try:
        target = Stage(stage)
    except ValueError:
        valid = ", ".join(s.value for s in Stage)
        return LeadOpResult(ok=False, detail=f"unknown stage '{stage}'; valid: {valid}",
                            lead_id=lead.id)
    from_stage = str(lead.stage)
    reason = f"move_lead → {target.value}" + (f": {note}" if note else "")
    await _journal(session, lead, target, reason)
    lead.stage = target
    if target == Stage.MANAGER:
        lead.agent_enabled = False
    elif target not in BOT_SILENT_STAGES:
        lead.agent_enabled = True
    session.add(lead)
    await session.flush()
    return _result(lead, from_stage, reason)


async def close_deal(
    session: AsyncSession, lead: Lead, note: str | None = None,
) -> LeadOpResult:
    """Deal won: hand the lead off and stop the bot (the sale is closed elsewhere)."""
    from_stage = str(lead.stage)
    reason = "close_deal (won)" + (f": {note}" if note else "")
    await _journal(session, lead, Stage.HANDED_OFF, reason)
    lead.stage = Stage.HANDED_OFF
    lead.agent_enabled = False
    session.add(lead)
    await session.flush()
    return _result(lead, from_stage, reason)


async def _newest_thread_id(session: AsyncSession, lead_id: int) -> int | None:
    row = (await session.execute(
        text("SELECT id FROM channel_thread WHERE lead_id = :l ORDER BY id DESC LIMIT 1"),
        {"l": lead_id},
    )).first()
    return row[0] if row else None


async def _lang(session: AsyncSession, lead: Lead) -> str:
    if lead.preferred_language:
        return lead.preferred_language
    branch = await session.get(Branch, lead.branch_id)
    return branch.lang if branch is not None else "id"


async def call_failed(
    session: AsyncSession, lead: Lead, note: str | None, llm,  # noqa: ANN001
) -> LeadOpResult:
    """Couldn't reach the lead by phone: journal it, re-arm the bot, and have Stepan
    proactively message the lead to continue in chat. A lead parked in a bot-silent /
    human-led stage is pulled back to QUALIFYING so the bot actually works it again."""
    from_stage = str(lead.stage)
    target = lead.stage
    if lead.stage in BOT_SILENT_STAGES or lead.stage in HUMAN_LED_STAGES:
        target = Stage.QUALIFYING
    reason = "call_failed" + (f": {note}" if note else "")
    await _journal(session, lead, target, reason)
    lead.stage = target
    lead.agent_enabled = True
    session.add(lead)
    await session.flush()

    queued = await _queue_call_failed_message(session, lead, llm)
    detail = reason + (" · bot will message the lead" if queued
                       else " · no chat thread to message")
    return _result(lead, from_stage, detail, queued=queued)


async def _queue_call_failed_message(
    session: AsyncSession, lead: Lead, llm,  # noqa: ANN001
) -> bool:
    """Generate one on-persona 'I tried to call, let's chat here' message and queue it.
    Reuses the reply DecisionEngine so the message carries KB context and the lead's
    language, exactly like a follow-up nudge."""
    thread_id = await _newest_thread_id(session, lead.id)
    if thread_id is None:
        return False
    # local imports break an import cycle (conversation ← leads) — same pattern as coach
    from app.modules.conversation.decision import generate  # noqa: PLC0415
    from app.modules.conversation.delivery import _BUBBLE_GAP_S, _split_bubbles  # noqa: PLC0415
    from app.modules.conversation.dossier import merge_dossier  # noqa: PLC0415
    from app.modules.conversation.engine import DecisionEngine, _fmt_llm_meta  # noqa: PLC0415
    from app.modules.conversation.free_mode import build_messages_free  # noqa: PLC0415
    from app.modules.conversation.repository import DossierRepo  # noqa: PLC0415
    from app.modules.conversation.routing import SMART  # noqa: PLC0415
    from app.modules.knowledge.service import KnowledgeService  # noqa: PLC0415
    from app.modules.knowledge.source import effective_kb_branch  # noqa: PLC0415

    lang = await _lang(session, lead)
    kb = await effective_kb_branch(session, lead.branch_id)
    engine = DecisionEngine(session, lead.branch_id, llm,
                            KnowledgeService(session, kb, llm))
    ctx = await engine.prepare(thread_id, workflow="call_failed")
    if ctx is None:
        return False
    dossiers = DossierRepo(session, lead.branch_id)
    stored = await dossiers.load(lead.id)
    try:
        messages = build_messages_free(
            await engine.free_kb_context(),
            ctx.dialog, lang, stored,
            now_block=await engine._now_block())  # noqa: SLF001
        messages.append({"role": "user", "content": _CALL_FAILED_NUDGE.format(lang=lang)})
        # A lead we already tried to phone is well past small talk — worth the strong model.
        decision, meta = await generate(
            engine, ctx, messages, thread_id, workflow="call_failed",
            capability=SMART, branch_id=lead.branch_id)
    except Exception:  # noqa: BLE001 — a generation failure must not undo the funnel move
        logger.exception("call_failed message gen failed lead=%d", lead.id)
        return False
    if decision is None or not decision.reply.strip():
        return False
    await dossiers.save(lead.id, merge_dossier(stored, decision.dossier))
    now = _now()
    meta_line = _fmt_llm_meta(meta)
    for i, bubble in enumerate(_split_bubbles(decision.reply)):
        session.add(Outbox(
            branch_id=lead.branch_id, thread_id=thread_id, text=bubble,
            source="call_failed", scheduled_at=now + timedelta(seconds=i * _BUBBLE_GAP_S),
            llm_info=meta_line,
        ))
    await session.flush()
    return True

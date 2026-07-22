"""Sales-sim: talk to Stepan as a lead through the REAL reply path (chat:smart), not the
coach. For testing his answers against the KB without touching production or IG.

A sandbox channel/lead/thread per (branch, session_key) is isolated three ways so the
worker never touches it and nothing is ever sent: the channel is inactive (ingest skips
it), the lead has agent_enabled=False (reply_pending skips it), and no outbox row is ever
created (send_outbox has nothing to send). say() just persists the turn as messages so
context accrues, runs ReplyService.decide(), and returns the decision.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Channel, ChannelThread, Lead, Message
from app.domain.clock import utc_now
from app.domain.enums import ChannelKind, Stage
from app.modules.knowledge.service import KnowledgeService
from app.modules.settings.service import get_settings
from app.ports.llm import LLMPort

from .factory import build_reply_service
from .reply import _split_bubbles

_SIM_HANDLE = "__sim__"


async def _sandbox(session: AsyncSession, branch_id: int, key: str) -> ChannelThread:
    ch = (await session.execute(select(Channel).where(
        Channel.branch_id == branch_id, Channel.handle == _SIM_HANDLE))).scalars().first()
    if ch is None:
        ch = Channel(branch_id=branch_id, kind=ChannelKind.INSTAGRAM, handle=_SIM_HANDLE,
                     is_active=False)  # inactive → worker ingest skips it
        session.add(ch)
        await session.flush()
    ext = f"sim:{key}"
    th = (await session.execute(select(ChannelThread).where(
        ChannelThread.channel_id == ch.id,
        ChannelThread.external_thread_id == ext))).scalars().first()
    if th is not None:
        return th
    lead = Lead(branch_id=branch_id, display_name=f"SIM:{key}",
                ig_username=f"__sim_{key}", agent_enabled=False,  # human-off → worker skips
                stage=Stage.NEW)
    session.add(lead)
    await session.flush()
    th = ChannelThread(lead_id=lead.id, channel_id=ch.id, external_thread_id=ext)
    session.add(th)
    await session.flush()
    return th


async def _next_at(session: AsyncSession, thread_id: int):  # noqa: ANN202
    n = (await session.execute(
        select(Message.id).where(Message.thread_id == thread_id))).all()
    return utc_now() + timedelta(seconds=len(n)), len(n)


class SimService:
    def __init__(self, session: AsyncSession, llm: LLMPort) -> None:
        self.session = session
        self.llm = llm

    async def say(self, branch_id: int, key: str, text: str) -> dict:
        """One sim turn: append the lead line, run the real decision, persist the reply."""
        th = await _sandbox(self.session, branch_id, key)
        at, n = await _next_at(self.session, th.id)
        self.session.add(Message(
            branch_id=branch_id, thread_id=th.id, channel_id=th.channel_id,
            external_id=f"sim-{th.id}-in-{n}",  # unique across sim threads sharing one channel
            direction="in", sent_by="lead", text=text, occurred_at=at))
        th.last_in_at = at
        self.session.add(th)
        await self.session.flush()

        cfg = await get_settings(self.session, branch_id)
        from app.modules.knowledge.source import effective_kb_branch  # noqa: PLC0415
        kb = await effective_kb_branch(self.session, branch_id)
        reply = build_reply_service(self.session, branch_id, self.llm,
                                    KnowledgeService(self.session, kb, self.llm), cfg,
                                    engine=cfg.reply_engine)
        decision = await reply.decide(th.id, workflow="sim")  # sim-tagged log, billed
        if decision is None:
            return {"ok": False, "detail": "no decision (over budget / empty / voice-hold)"}

        bubbles = _split_bubbles(decision.reply)
        base = at + timedelta(seconds=1)
        for i, b in enumerate(bubbles):
            self.session.add(Message(
                branch_id=branch_id, thread_id=th.id, channel_id=th.channel_id,
                external_id=f"sim-{th.id}-out-{n}-{i}", direction="out", sent_by="agent",
                text=b, occurred_at=base + timedelta(seconds=i),
                llm_info=reply._last_llm_meta.get("model")))  # noqa: SLF001
        th.last_out_at = base
        # Persist the segment + stage the model decided so multi-turn funnel movement and
        # segmentation are visible (decide() alone doesn't apply them — that's enqueue's job).
        lead = await self.session.get(Lead, th.lead_id)
        if lead is not None:
            if decision.lead_type:
                lead.lead_type = decision.lead_type
            if decision.audience:
                lead.audience = decision.audience
            lead.stage = decision.stage
            self.session.add(lead)
        self.session.add(th)
        await self.session.flush()

        return {
            "ok": True, "thread_id": th.id, "reply": decision.reply,
            "stage": str(decision.stage), "product": decision.product_slug,
            "lead_type": decision.lead_type, "audience": decision.audience,
            "ready": decision.ready, "needs_manager": decision.needs_manager,
            "discovery_complete": decision.discovery_complete,
            "jobs": decision.jobs, "pains": decision.pains, "gains": decision.gains,
            "meta": reply._last_llm_meta,  # noqa: SLF001
        }

    async def reset(self, branch_id: int, key: str) -> dict:
        """Wipe a sim thread's messages so the next say() starts a fresh conversation."""
        th = (await self.session.execute(select(ChannelThread).where(
            ChannelThread.external_thread_id == f"sim:{key}"))).scalars().first()
        if th is None:
            return {"ok": True, "detail": "nothing to reset"}
        msgs = (await self.session.execute(
            select(Message).where(Message.thread_id == th.id))).scalars().all()
        for m in msgs:
            await self.session.delete(m)
        lead = await self.session.get(Lead, th.lead_id)
        if lead is not None:
            lead.needs = None
            lead.stage = Stage.NEW
            self.session.add(lead)
        await self.session.flush()
        return {"ok": True, "detail": f"reset {len(msgs)} messages"}

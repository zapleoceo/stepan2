"""CrmRescueService — pick up the leads the phone couldn't reach.

The CRM's calls log shows who the branch dialed and never got through to (41% of a
month's requests in the audit). For each fresh missed call whose phone matches a Stepan
lead WITH an existing chat, run the standard call_failed op: journal it, re-arm the bot,
and have Stepan write the lead to continue in chat ("tried to call you — happy to help
right here"). The outbox CRM gate re-checks the CRM before the send actually goes out.

Guard-rails (each one is a real safety property, not decoration):
  - only during branch working hours (a rescue DM at 3am is a spam report)
  - skips leads a human took over (agent_enabled=False) and blocked leads
  - skips threads Stepan messaged in the last 48h (already engaging — don't pile on)
  - one rescue per lead per cooldown window (journal-based, survives restarts)
  - hard cap per run; sends still pass the outbox hourly/daily caps and the CRM gate
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead
from app.domain.clock import branch_now, utc_now
from app.modules.crm.gate import build_crm_reader, crm_read_url
from app.modules.crm.policy import policy_for
from app.modules.leads import ops
from app.modules.leads.lookup import AmbiguousPhone, find_lead
from app.modules.settings.service import get_settings
from app.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_PER_RUN_CAP = 2          # rescues per hourly tick — a trickle, not a blast (anti-ban)
_COOLDOWN_DAYS = 7        # one rescue attempt per lead per week
_RECENT_OUT_H = 48        # thread Stepan wrote to this recently = already engaging
_WORK_START_H, _WORK_END_H = 9, 20
_NOTE_PREFIX = "CRM missed call"  # dedup key in the stage_event journal — keep stable


class CrmRescueService:
    def __init__(self, session: AsyncSession, branch_id: int, llm: LLMPort) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm

    async def run(self) -> int:
        cfg = await get_settings(self.session, self.branch_id)
        url = crm_read_url(cfg)
        if not cfg.crm_rescue_enabled or not cfg.agent_enabled or not url:
            return 0
        if not _WORK_START_H <= branch_now(cfg.tz_offset_h).hour < _WORK_END_H:
            return 0
        reader = build_crm_reader(cfg)
        lister = getattr(reader, "list_missed_out_calls", None)
        if lister is None:  # REST source has no calls log — rescue is MCP-only
            return 0
        rescued = 0
        for phone, missed_at in await lister(url):
            if rescued >= _PER_RUN_CAP:
                break
            try:
                if await self._rescue_one(phone, missed_at):
                    rescued += 1
            except Exception:
                logger.exception("crm rescue failed branch=%d phone=%s",
                                 self.branch_id, phone[-4:])
        if rescued:
            logger.info("crm rescue branch=%d: %d leads picked up", self.branch_id, rescued)
        return rescued

    async def run_followthrough(self) -> int:
        """Отработать то, что менеджер записал в CRM, по таблице политик.

        Один проход на все результаты, а не крон на каждый: сегодня их три (думает,
        следующий набор, отказ), и четыре почти одинаковых задачи со своими копиями
        ограничителей разъехались бы ровно так же, как разъехались два сгона в CRM.

        Берём уже закэшированный crm_lead_state — он обновляется гейтом и pull-синком, так
        что ходить в CRM отсюда незачем. Идемпотентность по паре (лид, статус): пока
        менеджер не поставил новый результат, второй раз не пишем."""
        cfg = await get_settings(self.session, self.branch_id)
        if not cfg.crm_rescue_enabled or not cfg.agent_enabled:
            return 0
        if not _WORK_START_H <= branch_now(cfg.tz_offset_h).hour < _WORK_END_H:
            return 0
        branch = await self.session.get(Branch, self.branch_id)
        lang = (branch.lang if branch is not None else "id") or "id"
        rows = (await self.session.execute(text(
            "SELECT s.lead_id, s.status FROM crm_lead_state s"
            " JOIN lead l ON l.id = s.lead_id"
            " WHERE s.branch_id = :b AND s.status IS NOT NULL"
            "   AND l.is_blocked = false AND l.agent_enabled = true"
            " ORDER BY s.fetched_at DESC LIMIT 100"),
            {"b": self.branch_id})).all()
        acted = 0
        for lead_id, status in rows:
            if acted >= _PER_RUN_CAP:
                break
            policy = policy_for(status)
            if policy is None or not policy.initiates:
                continue
            if await self._already_acted(lead_id, status) or await self._recently_messaged(
                    lead_id):
                continue
            lead = await self.session.get(Lead, lead_id)
            if lead is None:
                continue
            try:
                res = await ops.crm_followthrough(
                    self.session, lead, status, policy.goal(lang), self.llm)
            except Exception:
                logger.exception("crm followthrough failed branch=%d lead=%s",
                                 self.branch_id, lead_id)
                continue
            if res.ok and res.message_queued:
                acted += 1
        if acted:
            logger.info("crm followthrough branch=%d: %d leads picked up",
                        self.branch_id, acted)
        return acted

    async def _already_acted(self, lead_id: int, status: str) -> bool:
        row = (await self.session.execute(
            text("SELECT 1 FROM stage_event WHERE lead_id = :l AND reason = :r LIMIT 1"),
            {"l": lead_id, "r": f"crm_followthrough: {status}"},
        )).first()
        return row is not None

    async def _rescue_one(self, phone: str, missed_at: str) -> bool:
        try:
            lead = await find_lead(self.session, phone, self.branch_id)
        except AmbiguousPhone:
            # Two of this branch's leads share the number's national digits. Picking one
            # would DM a stranger about a call they never got — skip and let a human sort it.
            logger.warning("crm rescue branch=%d: phone …%s matches several leads, skipped",
                           self.branch_id, phone[-4:])
            return False
        if lead is None or lead.is_blocked or not lead.agent_enabled:
            return False  # unknown to Stepan, or a human explicitly owns/stopped it
        if await self._recently_rescued(lead.id):
            return False
        if await self._recently_messaged(lead.id):
            return False
        note = f"{_NOTE_PREFIX} {missed_at[:10]}"
        res = await ops.call_failed(self.session, lead, note, self.llm)
        return bool(res.ok and res.message_queued)

    async def _recently_rescued(self, lead_id: int | None) -> bool:
        cutoff = utc_now() - timedelta(days=_COOLDOWN_DAYS)
        row = (await self.session.execute(
            text("SELECT 1 FROM stage_event WHERE lead_id = :l"
                 "  AND reason LIKE :pat AND created_at > :cut LIMIT 1"),
            {"l": lead_id, "pat": f"call_failed: {_NOTE_PREFIX}%", "cut": cutoff},
        )).first()
        return row is not None

    async def _recently_messaged(self, lead_id: int | None) -> bool:
        """True when Stepan wrote to (or has a line queued for) this lead recently."""
        cutoff = utc_now() - timedelta(hours=_RECENT_OUT_H)
        row = (await self.session.execute(
            text("SELECT 1 FROM channel_thread ct WHERE ct.lead_id = :l AND ("
                 "  ct.last_out_at > :cut"
                 "  OR EXISTS (SELECT 1 FROM outbox o WHERE o.thread_id = ct.id"
                 "             AND o.status = 'pending')) LIMIT 1"),
            {"l": lead_id, "cut": cutoff},
        )).first()
        return row is not None

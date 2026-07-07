"""OutboxSender — the single egress: drain one queued line through the channel.

Channel stays behind ChannelPort (injected, faked in tests). On success the sent text
is recorded as an outgoing Message so it becomes part of the dialog; on failure the row
is marked failed with the error and nothing is recorded. Hourly/daily send caps are
enforced here (the single egress) for anti-ban — automated lines are held back when the
branch is over budget; manager-sent lines bypass the cap (human override)."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Lead, Message, Outbox, StageEvent
from app.config import settings
from app.domain.clock import branch_day_start_utc
from app.domain.enums import Stage
from app.modules.settings.service import get_settings
from app.ports.channel import ChannelPort

from .repository import MessageRepo, OutboxRepo, ThreadRepo

logger = logging.getLogger(__name__)




# IG/WA soft blocks (challenge, rate limit, transient) — retry later, don't drop the line.
_SOFT_BLOCK = (
    "challenge", "feedback_required", "login_required", "checkpoint", "please wait",
    "rate", "429", "spam", "blocked", "try again", "throttl", "temporarily",
)
_RETRY_AFTER = timedelta(minutes=settings().soft_block_retry_min)
# A soft block used to retry forever — a PERMANENT block (never lifts) would requeue every
# _RETRY_AFTER indefinitely, hammering the channel and never surfacing to a human. Cap it;
# past this the row gives up as 'failed' like any other unrecoverable send error.
_MAX_SOFT_BLOCK_ATTEMPTS = settings().outbox_max_soft_block_attempts
# Humanlike pause after opening a chat before replying (anti-ban) — S1 parity.
_SEEN_DELAY_S = (settings().seen_delay_min_s, settings().seen_delay_max_s)


def _is_soft_block(error: str | None) -> bool:
    """True when a send error is transient (back off + retry) vs a hard, give-up failure."""
    low = (error or "").lower()
    return any(token in low for token in _SOFT_BLOCK)


class OutboxSender:
    """Send the next pending outbox row of one branch's thread via the channel."""

    def __init__(
        self, session: AsyncSession, branch_id: int, channel: ChannelPort
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.channel = channel
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)

    async def send_next(self, thread_id: int) -> Outbox | None:
        """Pick the oldest due line (scheduled_at ≤ now) and send it, unless capped."""
        row = await self.outbox.oldest_pending(thread_id)
        if row is None:
            return None
        now = datetime.now(UTC).replace(tzinfo=None)
        if row.scheduled_at > now:
            return None  # not due yet — respect reply delay
        cfg = await get_settings(self.session, self.branch_id)
        if row.source == "followup" and cfg.is_quiet_hour():
            return None  # follow-ups hold at night; live replies still go out (S1)
        if row.source != "manager" and await self._cap_reached(now, cfg):
            logger.info(
                "outbox hold branch=%d thread=%d: send cap reached", self.branch_id, thread_id
            )
            return None  # hourly/daily send cap hit — leave queued for a later tick
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None

        if cfg.crm_read_enabled and row.source != "manager":
            skipped = await self._crm_gate(thread, row)
            if skipped is not None:
                return skipped

        await self._humanize(thread.external_thread_id)
        result = await self.channel.send_text(thread.external_thread_id, row.text)
        if result.ok:
            row.status = "sent"
            row.sent_at = now
            row.error = None
            await self.messages.add(self._outgoing(thread, row, result.external_message_id))
            thread.last_out_at = now  # reply-loop watermark — bot no longer "owes" a reply
            await self._plan_followup(thread, row, cfg, now)
            self.session.add(thread)
            logger.info(
                "sent branch=%d thread=%d source=%s", self.branch_id, thread_id, row.source
            )
        elif _is_soft_block(result.error) and row.attempts < _MAX_SOFT_BLOCK_ATTEMPTS:
            row.status = "pending"  # transient (challenge/rate) — back off, don't drop
            row.scheduled_at = now + _RETRY_AFTER
            row.error = result.error
            row.attempts += 1
            logger.warning(
                "soft-block branch=%d thread=%d attempt=%d/%d: %s — retry at %s",
                self.branch_id, thread_id, row.attempts, _MAX_SOFT_BLOCK_ATTEMPTS,
                result.error, row.scheduled_at,
            )
        else:
            if _is_soft_block(result.error):
                logger.error(
                    "soft-block branch=%d thread=%d: %d attempts exhausted, giving up: %s",
                    self.branch_id, thread_id, row.attempts, result.error,
                )
            row.status = "failed"
            row.error = result.error
            logger.warning(
                "send failed branch=%d thread=%d: %s", self.branch_id, thread_id, result.error
            )
        self.session.add(row)
        await self.session.flush()
        return row

    async def _crm_gate(self, thread, row: Outbox) -> Outbox | None:
        """Consult the CRM before sending: a `hold` verdict skips this line (won't
        resend) and stands the lead down. Returns the row when skipped, else None."""
        from app.adapters.crm import CrmReader  # noqa: PLC0415 (optional dep, keep lazy)
        from app.modules.crm.gate import CrmGate  # noqa: PLC0415

        lead = await self.session.get(Lead, thread.lead_id)
        if lead is None:
            return None
        allowed, reason = await CrmGate(
            self.session, self.branch_id, CrmReader()).allow_send(lead, row.source)
        if allowed:
            return None
        row.status = "skipped"  # not 'pending' → oldest_pending never re-picks it
        row.error = f"crm hold: {reason}" if reason else "crm hold"
        self.session.add(row)
        await self.session.flush()
        logger.info("outbox skip branch=%d thread=%d: CRM hold (%s)",
                    self.branch_id, thread.id, reason)
        return row

    async def _humanize(self, external_thread_id: str) -> None:
        """Read the chat, then pause like a human before replying (anti-ban).

        Only for channels that support a read receipt (IG); a no-op elsewhere. The pause
        is deliberate — a reply that lands the same instant we 'saw' the message is a bot
        tell. mark_seen failures never block the send."""
        seen = getattr(self.channel, "mark_seen", None)
        if seen is None:
            return
        await seen(external_thread_id)
        await asyncio.sleep(random.uniform(*_SEEN_DELAY_S))  # noqa: S311 — timing, not crypto

    async def _plan_followup(self, thread, row: Outbox, cfg, now: datetime) -> None:
        """After a bot send: arm the next follow-up step, or close the cycle.

        S1 semantics — the timer counts from the bot's last message, indexed by
        followups_sent; the last follow-up of the schedule puts the lead to dormant.
        Manager sends do not touch the cycle."""
        if row.source not in ("agent", "followup"):
            return
        lead = await self.session.get(Lead, thread.lead_id)
        if lead is not None and (not lead.agent_enabled or lead.stage == Stage.DORMANT):
            thread.next_followup_at = None  # bot off / hard-stopped / handed off — no nudges
            return
        if row.source == "followup":
            thread.followups_sent += 1  # this nudge counts now that it actually went out
        schedule = cfg.followup_schedule_h
        if cfg.followup_enabled and schedule and thread.followups_sent < len(schedule):
            thread.next_followup_at = now + timedelta(
                hours=schedule[thread.followups_sent]
            )
            return
        thread.next_followup_at = None
        if row.source == "followup" and schedule and thread.followups_sent >= len(schedule):
            await self._to_dormant(thread, now)

    async def _to_dormant(self, thread, now: datetime) -> None:
        """Schedule exhausted, lead still silent → dormant (+ journal entry)."""
        lead = await self.session.get(Lead, thread.lead_id)
        if lead is None or lead.stage == Stage.DORMANT:
            return
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
            from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
            actor="system", reason="followup schedule exhausted", created_at=now,
        ))
        lead.stage = Stage.DORMANT
        self.session.add(lead)
        logger.info("branch=%d lead=%d → dormant (followups exhausted)",
                    self.branch_id, lead.id)

    async def _cap_reached(self, now: datetime, s) -> bool:
        """True when the branch already hit its hourly or daily send cap (cap ≤ 0 = off)."""
        if s.hourly_cap > 0:
            if await self.outbox.count_sent_since(now - timedelta(hours=1)) >= s.hourly_cap:
                return True
        if s.daily_cap > 0:
            day_start = branch_day_start_utc(now, s.tz_offset_h)
            if await self.outbox.count_sent_since(day_start) >= s.daily_cap:
                return True
        return False

    def _outgoing(self, thread, row: Outbox, external_id: str | None) -> Message:
        sent_by = row.source if row.source in ("manager", "agent") else "agent"
        return Message(
            branch_id=self.branch_id,
            thread_id=row.thread_id,
            channel_id=thread.channel_id,
            external_id=external_id or f"out-{row.id}",
            direction="out",
            sent_by=sent_by,
            text=row.text,
            llm_info=row.llm_info,
        )

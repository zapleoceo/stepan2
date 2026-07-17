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
from app.domain.enums import ChannelKind, Stage
from app.modules.settings.service import get_channel_settings
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
        now = datetime.now(UTC).replace(tzinfo=None)
        await self._sweep_stale_claims(thread_id, now)
        row = await self.outbox.oldest_pending(thread_id)
        if row is None:
            return None
        if row.scheduled_at > now:
            return None  # not due yet — respect reply delay
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        # Resolve settings for THIS thread's connector: anti-ban caps, sending toggle and the
        # follow-up schedule are per-channel (Meta's official API needs no anti-ban throttle
        # and closes its window in ~24h, so its cadence differs from Instagram's). Branch-scope
        # keys (quiet hours, tz) come through unchanged.
        cfg = await get_channel_settings(self.session, self.branch_id, thread.channel_id)
        if not cfg.sending_enabled:
            return None  # connector sending paused (soft-block) — queue keeps building
        if row.source == "followup" and cfg.is_quiet_hour():
            return None  # follow-ups hold at night; live replies still go out (S1)
        if row.source != "manager" and await self._cap_reached(now, cfg, thread.channel_id):
            logger.info(
                "outbox hold branch=%d thread=%d: send cap reached", self.branch_id, thread_id
            )
            return None  # hourly/daily send cap hit — leave queued for a later tick

        # Meta closes the standard messaging window ~24h after the lead's last message; an
        # AUTOMATED send into a closed window is rejected by Graph, so skip the doomed API call
        # and mark it skipped (not failed — it's expected, not a manager-facing error; the
        # follow-up cycle resumes when the lead writes again and ingest re-opens the window).
        # A MANAGER send still attempts: a human agent may deliver via the 7-day human_agent tag,
        # and the real result surfaces to them (see the failed-send bubble).
        if (getattr(self.channel, "kind", None) == ChannelKind.META_BUSINESS
                and row.source != "manager"
                and thread.window_until is not None and thread.window_until < now):
            row.status = "skipped"
            row.error = "meta_window_closed"
            self.session.add(row)
            # Pause the thread — the window won't reopen until the lead writes again, so
            # regenerating a reply every tick is pure token burn (see _pause_dormant).
            await self._pause_dormant(thread, "Meta 24h window closed — paused until lead writes")
            await self.session.flush()
            logger.info("outbox skip branch=%d thread=%d: Meta 24h window closed",
                        self.branch_id, thread_id)
            return row

        if cfg.crm_read_enabled and row.source != "manager":
            skipped = await self._crm_gate(thread, row)
            if skipped is not None:
                return skipped

        # Claim the row in its OWN committed step before the network call. The IG send and
        # the bookkeeping used to share one transaction: anything crashing AFTER a successful
        # send rolled the row back to 'pending' and the next tick delivered the same text
        # again (threads 2697/4122, 2026-07-17: identical bubbles ~80s apart, one outbox
        # row). A committed 'sending' row is invisible to oldest_pending, so no tick can
        # re-send it; if the final status never lands, the sweep marks it failed — an
        # unknown outcome is never retried (a possible lost line beats a certain duplicate).
        row.status = "sending"
        row.sent_at = now  # claim timestamp — the stale-claim sweep keys off it
        self.session.add(row)
        await self.session.commit()
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
            row.sent_at = None  # the claim stamp is not a delivery time
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
            row.sent_at = None  # the claim stamp is not a delivery time
            row.error = result.error
            logger.warning(
                "send failed branch=%d thread=%d: %s", self.branch_id, thread_id, result.error
            )
            if row.source == "manager":
                pass  # a human is driving; the failed-send bubble surfaces to them, don't pause
            else:
                # A live reply / nudge that permanently failed (400, unrecoverable) must NOT
                # leave the thread "awaiting" — otherwise the dispatcher regenerates a fresh
                # (equally undeliverable) reply every tick, burning tokens and piling up
                # failed rows (the Meta 400 loop). Pause to dormant; a fresh inbound revives
                # it. This supersedes the follow-up re-arm: a paused thread has no timer.
                await self._pause_dormant(
                    thread, f"send failed (undeliverable): {(result.error or '')[:120]}")
        self.session.add(row)
        await self.session.flush()
        return row

    async def _sweep_stale_claims(self, thread_id: int, now: datetime) -> None:
        """A row stuck in 'sending' means we crashed between the IG call and the final
        status — the message may or may not have reached the lead. Never resend it (the
        duplicate is the worse failure); after 10 minutes mark it failed so the queue moves
        on and the error is visible in the UI."""
        from sqlalchemy import update  # noqa: PLC0415
        await self.session.execute(
            update(Outbox)
            .where(Outbox.thread_id == thread_id, Outbox.status == "sending",
                   Outbox.sent_at < now - timedelta(minutes=10))
            .values(status="failed",
                    error="crashed mid-send — outcome unknown, not retried"))

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

    async def _pause_dormant(self, thread, reason: str) -> None:
        """A live reply/nudge could NOT be delivered (Meta 24h window closed, or a permanent
        send error like a Graph 400) — move the lead to DORMANT with a journal entry and clear
        the follow-up timer. Without this the thread's last_out_at never advances, so it stays
        "awaiting reply", the dispatcher re-picks it every tick, and Stepan burns tokens
        generating a fresh draft that ALSO can't be sent — the exact loop that piled up 400s
        on the Meta channel (2026-07-10). Dormant drops it out of threads_awaiting_reply; a
        fresh inbound revives it (ingest._revive_bot → qualifying) and, for Meta, re-opens the
        window so the next send goes through. A human-led / already-silent stage is left
        alone — a delivery hiccup must not yank a lead a manager owns."""
        from app.domain.enums import HUMAN_LED_STAGES  # noqa: PLC0415
        lead = await self.session.get(Lead, thread.lead_id)
        if lead is None or lead.stage == Stage.DORMANT or lead.stage in HUMAN_LED_STAGES:
            return
        self.session.add(StageEvent(
            branch_id=self.branch_id, lead_id=lead.id, thread_id=thread.id,
            from_stage=str(lead.stage), to_stage=str(Stage.DORMANT),
            actor="system", reason=reason,
        ))
        lead.stage = Stage.DORMANT
        lead.agent_enabled = False  # keep the bot-on/off flag consistent with the dormant stage
        thread.next_followup_at = None
        self.session.add(lead)
        self.session.add(thread)
        logger.info("branch=%d thread=%d → dormant (undeliverable): %s",
                    self.branch_id, thread.id, reason)

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
        lead.agent_enabled = False  # keep the bot-on/off flag consistent with the dormant stage
        self.session.add(lead)
        logger.info("branch=%d lead=%d → dormant (followups exhausted)",
                    self.branch_id, lead.id)

    async def _cap_reached(self, now: datetime, s, channel_id: int) -> bool:
        """True when THIS connector already hit its hourly or daily send cap (cap ≤ 0 = off).
        Counts are per-channel so one connector's volume never throttles another's."""
        if s.hourly_cap > 0:
            if await self.outbox.count_sent_since(
                    now - timedelta(hours=1), channel_id) >= s.hourly_cap:
                return True
        if s.daily_cap > 0:
            day_start = branch_day_start_utc(now, s.tz_offset_h)
            if await self.outbox.count_sent_since(day_start, channel_id) >= s.daily_cap:
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

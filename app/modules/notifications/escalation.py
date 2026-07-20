"""SLA escalation — a ready/handoff alert the manager hasn't worked within the SLA gets ONE
polite re-ping tagging the branch manager, so a hot lead never sits unattended (thread 1793:
the only ready lead of the day gave a phone, asked for the manager's number, and the thread
went quiet — the most expensive miss in the audit).

Fires ONCE per alert (reping_at guard), only when NO manager has replied in the thread since
the alert, and only inside the branch's local working-hours window (a ready lead at 2am waits
for the morning rather than buzzing the manager overnight)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead
from app.config import settings
from app.ports.notify import NotifierPort

from .alerts import _KIND_ICON

logger = logging.getLogger(__name__)


def _within_hours(local_hour: int, window: str) -> bool:
    """True if `local_hour` is inside the 'start-end' window (end exclusive)."""
    try:
        start, end = (int(x) for x in (window or "").split("-"))
    except (ValueError, TypeError):
        return True  # a malformed window never blocks a hot-lead nudge
    return start <= local_hour < end


class EscalationService:
    """Re-ping stale, unworked manager alerts for one branch."""

    def __init__(
        self, session: AsyncSession, branch_id: int, notifier: NotifierPort | None
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self._notifier = notifier

    async def run(self) -> int:
        if self._notifier is None:
            return 0
        cfg = settings()
        branch = await self.session.get(Branch, self.branch_id)
        tz = branch.tz_offset_h if branch is not None else 7
        now = datetime.now(UTC)
        if not _within_hours((now + timedelta(hours=tz)).hour, cfg.reping_hours_wib):
            return 0
        naive_now = now.replace(tzinfo=None)
        cutoff = naive_now - timedelta(minutes=cfg.alert_reping_after_min)
        floor = naive_now - timedelta(minutes=cfg.alert_reping_max_age_min)
        # Actionable, unworked, still-FRESH alerts: past the SLA (cutoff) but not older than the
        # ceiling (floor). The floor is critical — without it the first run re-pinged the entire
        # multi-week backlog of old unworked alerts at once (2026-07-20 incident: ~70 pings for
        # leads up to 16 days old). ready deal / open-house RSVP always; needs_manager only with a
        # phone. Skip any thread a manager already replied in since the alert — that IS the action.
        rows = (await self.session.execute(text(
            "SELECT a.id, a.thread_id, a.lead_id, a.kind, a.created_at, l.notify_topic_id "
            "FROM manager_alert a JOIN lead l ON l.id = a.lead_id "
            "WHERE a.branch_id = :bid AND a.reping_at IS NULL "
            "  AND a.created_at <= :cutoff AND a.created_at >= :floor "
            "  AND (a.kind IN ('ready_deal','ready_openhouse') "
            "       OR (a.kind = 'needs_manager' AND l.phone_e164 IS NOT NULL)) "
            "  AND NOT EXISTS (SELECT 1 FROM message m WHERE m.thread_id = a.thread_id "
            "       AND m.sent_by = 'manager' AND m.occurred_at > a.created_at) "
            "ORDER BY a.id LIMIT 20"),
            {"bid": self.branch_id, "cutoff": cutoff, "floor": floor})).all()
        sent = 0
        for alert_id, thread_id, lead_id, kind, created_at, topic_id in rows:
            if await self._reping_one(
                alert_id, thread_id, lead_id, kind, created_at, topic_id, naive_now):
                sent += 1
        if sent:
            logger.info("escalation: branch=%d re-pinged %d stale alert(s)", self.branch_id, sent)
        return sent

    async def _ensure_topic(self, lead_id: int, kind: str, topic_id: int | None) -> int | None:
        """The lead's forum topic, created if missing — a re-ping goes into the lead's OWN
        thread, exactly like the chat alert, never into the group's General channel."""
        assert self._notifier is not None
        if topic_id is not None:
            return topic_id
        lead = await self.session.get(Lead, lead_id)
        if lead is None:
            return None
        name = (lead.display_name or lead.ig_username or f"lead #{lead_id}").strip()
        new_id = await self._notifier.create_topic(name=name, icon_emoji=_KIND_ICON.get(kind))
        if new_id is not None:
            lead.notify_topic_id = new_id
            self.session.add(lead)
            await self.session.flush()
        return new_id

    async def _reping_one(
        self, alert_id: int, thread_id: int | None, lead_id: int, kind: str,
        created_at: datetime, topic_id: int | None, now: datetime,
    ) -> bool:
        assert self._notifier is not None
        if isinstance(created_at, str):  # SQLite returns DateTime columns as ISO strings
            created_at = datetime.fromisoformat(created_at)
        mins = max(1, int((now - created_at).total_seconds() // 60))
        body = self._compose(kind, mins, thread_id)
        topic_id = await self._ensure_topic(lead_id, kind, topic_id)
        if topic_id is None:  # no topic and couldn't open one — never fall back to General
            logger.warning("escalation: no topic for lead=%s — skipping re-ping", lead_id)
            return False
        status = await self._notifier.send(text=body, topic_id=topic_id)
        if status == "topic_gone":  # the lead's topic was deleted — recreate it and resend
            topic_id = await self._ensure_topic(lead_id, kind, None)
            status = (await self._notifier.send(text=body, topic_id=topic_id)
                      if topic_id is not None else status)
        if status != "ok":
            logger.warning("escalation: re-ping failed alert=%s status=%s", alert_id, status)
            return False
        await self.session.execute(text(
            "UPDATE manager_alert SET reping_at = :now WHERE id = :id"),
            {"now": now, "id": alert_id})
        return True

    def _compose(self, kind: str, mins: int, thread_id: int | None) -> str:
        tag = settings().manager_tag
        what = ("udah siap closing (ready)" if kind.startswith("ready")
                else "nunggu jawaban dari tim")
        line = (f"🔔 {tag}, lead ini {what} dan udah ~{mins} menit belum ada yang follow-up. "
                "Tolong dibantu ya kalau ada waktu 🙏")
        if thread_id is None:
            return line
        link = f"{settings().public_url.rstrip('/')}/ui/chat/{thread_id}"
        return f'{line}\n💬 <a href="{link}">buka chat</a>'

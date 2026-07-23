"""Alert service — records a manager hand-off AND pings the group, one message per lead
into that lead's own Telegram forum topic.

The message reads: branch-language chat summary, then the reason in the branch language,
then the same summary + reason in Russian, then a chat deep-link. Each lead gets its own
topic (created on first alert, recreated if it was deleted). Persisting the ManagerAlert
row and pinging live together so the CRM record and the ping never drift; the ping is
best-effort and never raises."""
from __future__ import annotations

import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, ManagerAlert
from app.adapters.db.repository import BranchScoped
from app.config import settings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from .summarize import build_alert_body

logger = logging.getLogger(__name__)

# Alert kind → forum-topic icon (matches the funnel semantics: a deal is 🔥, an
# open-house RSVP is a 📆, a manager question is a ❓).
_KIND_ICON = {
    "ready_deal": "🔥", "ready_openhouse": "📆", "needs_manager": "❓",
    "bot_off_message": "🔇", "non_target": "🚫", "unmapped_ad": "🏷️",
}
# Short language label shown before each summary block.
_LANG_LABEL = {"id": "Bahasa", "en": "En", "ru": "Ru", "ms": "Melayu"}


class AlertService:
    """Records and dispatches manager hand-offs for one branch."""

    def __init__(
        self, session: AsyncSession, branch_id: int, notifier: NotifierPort | None,
        llm: LLMPort | None = None,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self._notifier = notifier
        self._llm = llm
        self._alerts: BranchScoped[ManagerAlert] = BranchScoped(
            session, branch_id, model=ManagerAlert
        )

    async def raise_alert(
        self,
        lead_id: int,
        kind: str,
        summary_en: str,
        summary_ru: str,
        thread_id: int | None = None,
        lead_phone: str | None = None,
    ) -> ManagerAlert:
        """Write the branch-scoped alert row, then ping the lead's topic. summary_en /
        summary_ru are the REASON (why the bot escalated); the chat summary is generated."""
        alert = await self._alerts.add(
            ManagerAlert(
                branch_id=self.branch_id,
                lead_id=lead_id,
                thread_id=thread_id,
                kind=kind,
                lead_phone=lead_phone,
                summary_en=summary_en,
                summary_ru=summary_ru,
            )
        )
        if self._notifier is not None:  # row is the CRM record; the ping is best-effort
            try:
                await self._ping(lead_id, thread_id, kind, summary_en, summary_ru)
            except Exception:
                logger.warning("alert ping failed lead=%s", lead_id, exc_info=True)
        return alert

    async def _ping(
        self, lead_id: int, thread_id: int | None, kind: str,
        reason_en: str, reason_ru: str,
    ) -> None:
        assert self._notifier is not None
        branch = await self.session.get(Branch, self.branch_id)
        lang = branch.lang if branch is not None else "en"
        lead = await self.session.get(Lead, lead_id) if lead_id else None
        body = await build_alert_body(
            self.session, self._llm, thread_id,
            branch_lang=lang, reason_en=reason_en, reason_ru=reason_ru,
            branch_id=self.branch_id,
        )
        lead_name = (lead.display_name or lead.ig_username or f"lead #{lead_id}") if lead else ""
        text = self._compose(
            thread_id, lead_name, lang,
            body.summary_branch, body.reason_branch, body.summary_ru, reason_ru,
        )
        topic_id = lead.notify_topic_id if lead is not None else None
        if lead is not None and topic_id is None:
            topic_id = await self._open_topic(lead, kind)
        status = await self._notifier.send(text=text, topic_id=topic_id)
        if status == "topic_gone" and lead is not None:  # topic was deleted — recreate once
            topic_id = await self._open_topic(lead, kind)
            await self._notifier.send(text=text, topic_id=topic_id)

    async def _open_topic(self, lead: Lead, kind: str) -> int | None:
        """Create the lead's forum topic (icon by alert kind) and persist its id."""
        assert self._notifier is not None
        name = (lead.display_name or lead.ig_username or f"lead #{lead.id}").strip()
        topic_id = await self._notifier.create_topic(name=name, icon_emoji=_KIND_ICON.get(kind))
        if topic_id is not None:
            lead.notify_topic_id = topic_id
            self.session.add(lead)
            await self.session.flush()
        return topic_id

    def _compose(
        self, thread_id: int | None, lead_name: str, branch_lang: str,
        sum_branch: str, reason_branch: str, sum_ru: str, reason_ru: str,
    ) -> str:
        blabel = _LANG_LABEL.get((branch_lang or "").lower(), branch_lang or "?")
        head = ", ".join(p for p in (
            (f"чат #{thread_id}" if thread_id is not None else ""), _esc(lead_name)) if p)
        parts: list[str] = []
        if head:
            parts.append(f"<b>{head}</b>")
        if sum_branch.strip():
            parts.append(f"<b>{_esc(blabel)}:</b> {_esc(sum_branch.strip())}")
        parts.append(f"⚠️ {_esc(reason_branch.strip())}")
        parts.append("➖➖➖")
        if sum_ru.strip():
            parts.append(f"<b>Ru:</b> {_esc(sum_ru.strip())}")
        parts.append(f"⚠️ {_esc(reason_ru.strip())}")
        body = "\n\n".join(parts)
        if thread_id is not None:
            link = f"{settings().public_url.rstrip('/')}/ui/chat/{thread_id}"
            body += f'\n\n💬 <a href="{link}">open chat</a>'
        return body


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

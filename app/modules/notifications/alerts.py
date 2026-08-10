"""Alert service — records a manager hand-off AND pings the group, one message per lead
into that lead's own Telegram forum topic.

The message reads: branch-language chat summary, then the reason in the branch language,
then the same summary + reason in Russian, then a chat deep-link. Each lead gets its own
topic (created on first alert, recreated if it was deleted). Persisting the ManagerAlert
row and pinging live together so the CRM record and the ping never drift; the ping is
best-effort and never raises."""
from __future__ import annotations

import logging

from sqlalchemy import text as sql
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Lead, ManagerAlert
from app.adapters.db.repository import BranchScoped
from app.config import settings
from app.ports.llm import LLMPort
from app.ports.notify import NotifierPort

from .alert_reuse import AlertAction, plan_alert
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
    ) -> ManagerAlert | None:
        """Write the branch-scoped alert row, then ping the lead's topic. summary_en /
        summary_ru are the REASON (why the bot escalated); the chat summary is generated.

        A blocked lead produces nothing at all — no row, no ping. Blocking is the owner saying
        this thread is spam or abuse and is closed; every alert after that asks a human to look
        again at something they already judged. The check used to live in ONE caller
        (delivery.raise_manager_alert) while six others went straight past it, so a blocked lead
        writing again still pinged Telegram through `bot_off_message` — the loudest of the lot,
        because it fires on every single inbound."""
        lead = await self.session.get(Lead, lead_id)
        if lead is not None and lead.is_blocked:
            logger.info("alert %s suppressed branch=%d lead=%d — lead is blocked",
                        kind, self.branch_id, lead_id)
            return None
        if lead_phone is None and lead is not None:
            # Fall back to what the lead record holds. 29 live alerts carried an empty phone
            # for a lead who had one — every caller passes the number it happens to have in
            # scope, and a path that reached here without one handed the manager a card with
            # nothing to dial. The lead row is the one place the number is always current.
            lead_phone = lead.phone_e164
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
                alert.tg_message_id = await self._ping(
                    lead_id, thread_id, kind, summary_en, summary_ru)
                self.session.add(alert)
            except Exception:
                logger.warning("alert ping failed lead=%s", lead_id, exc_info=True)
        return alert

    async def _ping(
        self, lead_id: int, thread_id: int | None, kind: str,
        reason_en: str, reason_ru: str,
    ) -> int | None:
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
        card = self._compose(
            thread_id, lead_name, await self._connector(thread_id), lang,
            body.summary_branch, body.reason_branch, body.summary_ru, reason_ru,
            body.last_msg, body.last_msg_ru,
        )
        topic_id = lead.notify_topic_id if lead is not None else None
        if lead is not None and topic_id is None:
            topic_id = await self._open_topic(lead, kind)
        return await self._deliver(lead_id, thread_id, kind, lead, topic_id, card)

    async def _deliver(  # noqa: PLR0913 — шесть значений одного решения, дробить нечего
        self, lead_id: int, thread_id: int | None, kind: str,
        lead: Lead | None, topic_id: int | None, card: str,
    ) -> int | None:
        """Отправить карточку — или переиспользовать ту, что уже висит в топике лида.

        Раньше здесь был безусловный send, и топик превращался в ленту одинаковых карточек:
        по одному человеку их набегало столько, сколько случилось событий. Правило живёт в
        alert_reuse — менеджер ответил, значит прошлая отработана: убрать и прислать новую;
        не ответил — переписать ту, что уже на экране."""
        assert self._notifier is not None
        send = getattr(self._notifier, "send_returning_id", None)
        if send is None:  # нотифаер без правки сообщений — прежнее поведение, без потерь
            status = await self._notifier.send(text=card, topic_id=topic_id)
            if status == "topic_gone" and lead is not None:
                topic_id = await self._open_topic(lead, kind)
                await self._notifier.send(text=card, topic_id=topic_id)
            return None

        plan = plan_alert(**await self._previous(lead_id, thread_id))
        if plan.action is AlertAction.EDIT and plan.message_id is not None:
            edit = getattr(self._notifier, "edit_text", None)
            if edit is not None and await edit(message_id=plan.message_id, text=card):
                logger.info("alert updated in place lead=%d msg=%d", lead_id, plan.message_id)
                return plan.message_id
        elif plan.action is AlertAction.REPLACE and plan.message_id is not None:
            drop = getattr(self._notifier, "delete_message", None)
            if drop is not None:
                await drop(message_id=plan.message_id)
                logger.info("alert retired lead=%d msg=%d — manager already answered",
                            lead_id, plan.message_id)

        status, message_id = await send(text=card, topic_id=topic_id)
        if status == "topic_gone" and lead is not None:  # topic was deleted — recreate once
            topic_id = await self._open_topic(lead, kind)
            _status, message_id = await send(text=card, topic_id=topic_id)
        return message_id

    async def _previous(self, lead_id: int, thread_id: int | None) -> dict:
        """Прошлая карточка этого лида и время последней реплики ЧЕЛОВЕКА в треде."""
        prev = (await self.session.execute(
            sql("SELECT tg_message_id, created_at FROM manager_alert"
                " WHERE lead_id = :l AND branch_id = :b AND tg_message_id IS NOT NULL"
                " ORDER BY created_at DESC LIMIT 1"),
            {"l": lead_id, "b": self.branch_id})).first()
        replied = None
        if thread_id is not None:
            row = (await self.session.execute(
                sql("SELECT max(occurred_at) FROM message WHERE thread_id = :t"
                    " AND direction = 'out' AND sent_by = 'manager'"),
                {"t": thread_id})).first()
            replied = row[0] if row else None
        return {
            "previous_message_id": prev[0] if prev else None,
            "previous_at": prev[1] if prev else None,
            "manager_replied_at": replied,
        }

    async def _connector(self, thread_id: int | None) -> str:
        """Через какой аккаунт идёт разговор. У лида их бывает несколько, и «ответьте ему»
        без имени номера означает «найдите сами, где именно»."""
        if thread_id is None:
            return ""
        row = (await self.session.execute(
            sql("SELECT coalesce(c.handle, c.kind) FROM channel_thread ct"
                " JOIN channel c ON c.id = ct.channel_id WHERE ct.id = :t"),
            {"t": thread_id})).first()
        return (row[0] or "") if row else ""

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

    def _compose(  # noqa: PLR0913 — это одна карточка, дробить её на объекты незачем
        self, thread_id: int | None, lead_name: str, connector: str, branch_lang: str,
        sum_branch: str, reason_branch: str, sum_ru: str, reason_ru: str,
        last_msg: str = "", last_msg_ru: str = "",
    ) -> str:
        """Шапка отвечает на «куда идти» — номер чата, аккаунт, имя. Дальше два языковых
        блока, и в каждом сначала дословная реплика лида, потом пересказ: пересказ говорит
        о разговоре вообще, а решение менеджер принимает по тому, что человек написал
        прямо сейчас. Раньше за этой строчкой приходилось открывать чат."""
        blabel = _LANG_LABEL.get((branch_lang or "").lower(), branch_lang or "?")
        head = " · ".join(p for p in (
            (f"чат #{thread_id}" if thread_id is not None else ""),
            _esc(connector), _esc(lead_name)) if p)
        parts: list[str] = []
        if head:
            parts.append(f"<b>{head}</b>")
        if last_msg.strip():
            parts.append(f"💬 <i>«{_esc(last_msg.strip())}»</i>")
        if sum_branch.strip():
            parts.append(f"<b>{_esc(blabel)}:</b> {_esc(sum_branch.strip())}")
        parts.append(f"⚠️ {_esc(reason_branch.strip())}")
        parts.append("➖➖➖")
        if last_msg_ru.strip():
            parts.append(f"💬 <i>«{_esc(last_msg_ru.strip())}»</i>")
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

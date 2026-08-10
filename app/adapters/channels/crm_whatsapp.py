"""WhatsApp через sender CRM: входящие берём из своей таблицы, ответ шлём их инструментом.

`fetch_inbound` не ходит в сеть, и это главное решение здесь. Сообщение приносит колбек
(`/api/v1/sender/inbound-callback`) и кладёт в `sender_inbound`; адаптер разбирает накопленное
и отдаёт в том же виде, что и любой другой коннектор. Всё, что ниже по течению — создание
лида, дедупликация, стадии, ответ Степана — работает без единой правки, потому что не знает,
откуда взялось сообщение.

Альтернатива — опрашивать sender из адаптера — означала бы второй путь приёма, который
разошёлся бы с колбеком в том, что считать дублем, и превратил бы push в polling. Добор
пропущенного (catchup) наполняет ТУ ЖЕ таблицу и потому дублей не создаёт.

Отправка не подтверждает доставку: их `conversation/send` кладёт в очередь и отвечает сразу,
настоящий исход приходит позже. Поэтому спецификация коннектора объявляет
confirms_delivery=False, и outbox пишет `queued`, а не `sent`.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import SenderInbound
from app.adapters.sender_mcp import SenderMcp
from app.domain.clock import utc_now
from app.domain.enums import ChannelKind, SessionStatus
from app.modules.sender.tenant import SenderTenant
from app.ports.channel import InboundMessage, SendResult

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "sender mcp is not configured — no url or token"
NO_CONVERSATION = "no stored conversation for this thread"
_BATCH = 100


class CrmWhatsAppAdapter:
    """ChannelPort поверх sender CRM."""

    kind: ChannelKind = ChannelKind.CRM_WHATSAPP

    def __init__(self, session: AsyncSession, mcp: SenderMcp,
                 tenant: SenderTenant) -> None:
        self.session = session
        self.mcp = mcp
        self.tenant = tenant

    async def fetch_inbound(self) -> list[InboundMessage]:
        """Необработанные входящие из своей таблицы, помеченные как разобранные.

        Только `direction == 'in'`: исходящее — это менеджер (или эхо его сообщения из
        приложения), и подавать его как реплику лида значило бы дать Степану повод ответить
        человеку, который уже ведёт разговор.

        Помечаем ДО возврата, одной транзакцией с чтением: повторный проход не должен принести
        то же сообщение снова. Дубль на этом слое дороже пропуска — существующий конвейер
        отсеет его по external_id, но сначала успеет завести лишний ход диалога.
        """
        rows = (await self.session.execute(
            select(SenderInbound)
            .where(
                SenderInbound.processed_at.is_(None),  # type: ignore[union-attr]
                SenderInbound.direction == "in",  # type: ignore[arg-type]
            )
            .order_by(SenderInbound.id)  # type: ignore[arg-type]
            .limit(_BATCH)
        )).scalars().all()

        out: list[InboundMessage] = []
        now = utc_now()
        for row in rows:
            row.processed_at = now
            self.session.add(row)
            if not (row.conversation_id and (row.text or row.attachment)):
                # Нечего показывать или некуда отвечать: помечаем разобранным, чтобы не
                # крутилось вечно, но в диалог не отдаём.
                logger.info("sender inbound skipped external_id=%s: no text or conversation",
                            row.external_id)
                continue
            out.append(InboundMessage(
                external_thread_id=row.conversation_id,
                sender_id=row.phone or row.conversation_id,
                text=row.text or "",
                occurred_at=row.received_at,
                sender_name=row.from_name,
                lead_phone=row.phone,
                external_id=row.external_id,
                media_url=row.attachment,
                direction="in",
            ))
        await self.session.flush()
        return out

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        """Ответить в тот же разговор.

        Разрешения канал не объявляет: уйдёт ли строка — вопрос про ЛИДА, и ответ у него
        уже есть в стадии и тумблере бота (domain/funnel.py). Здесь только «умею ли я».

        Их инструмент адресует сообщение не одним идентификатором, а набором: `id` (chat_id),
        `conversationId`, проект, филиал и опциональный `userId`. Все они приходили в колбеке,
        поэтому берём их из последней сохранённой строки этого разговора — так адрес ответа
        всегда тот, по которому лид действительно писал.
        """
        if not (self.mcp.configured and self.tenant.configured):
            return SendResult(ok=False, error=NOT_CONFIGURED)

        row = (await self.session.execute(
            select(SenderInbound)
            .where(SenderInbound.conversation_id == external_thread_id)  # type: ignore[arg-type]
            .order_by(SenderInbound.id.desc())  # type: ignore[union-attr]
            .limit(1)
        )).scalars().first()
        if row is None or not row.chat_id:
            return SendResult(ok=False, error=NO_CONVERSATION)

        outcome = await self.mcp.send_text(self.tenant.send_args(
            chat_id=row.chat_id,
            conversation_id=external_thread_id,
            user_id=row.sender_user_id,
            text=text,
        ))
        if not outcome.accepted:
            return SendResult(ok=False, error=outcome.error)
        # ok=True означает «принято в очередь». Что это не доставка, знает спецификация
        # коннектора (confirms_delivery=False) — здесь врать про исход нечем и незачем.
        return SendResult(ok=True, external_message_id=outcome.ref)

    async def session_status(self) -> SessionStatus:
        """Живо ли подключение.

        PENDING, пока нет токена или чисел филиала: ровно так помечен канал, которым ещё
        нельзя пользоваться, и воркер по этому статусу не выдаёт порт (active_session_settings).
        Токен запросом не проверяем — статус спрашивают часто, а лишний обмен с чужим сервером
        ради галочки это плата без выгоды."""
        if not (self.mcp.configured and self.tenant.configured):
            return SessionStatus.PENDING
        return SessionStatus.ACTIVE

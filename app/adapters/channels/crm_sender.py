"""Переписка через sender CRM: входящие берём из своей таблицы, ответ шлём их инструментом.

Один адаптер на все их мессенджеры. У филиала включены whats-app, telegram, viber и
smsviber; какой именно принёс сообщение, сказано в поле `channel_name`, и ниже по течению
это никого не касается — адресация ответа идёт по разговору, а не по мессенджеру.

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
from app.domain.phone import from_wa_id
from app.modules.sender.tenant import SenderTenant
from app.ports.channel import InboundMessage, SendResult

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "sender mcp is not configured — no url or token"
NO_CONVERSATION = "no stored conversation for this thread"
# Приём включён, ответы — нет. Настройка филиала `sender_enabled`, и она нужна отдельно от
# общего тумблера бота: тот один на филиал, а Instagram там уже работает и глушить его,
# чтобы помолчать в CRM, нельзя. Читать переписку в общий тред лида можно задолго до того,
# как решено, кто в этом канале отвечает — там живые менеджеры.
REPLIES_OFF = "crm replies are disabled for this branch (sender_enabled)"
_BATCH = 100

# Мессенджеры, где адрес собеседника — это НОМЕР. Только у них поле `from` можно считать
# телефоном и пускать в ключ склейки лидов.
#
# Список, а не «непустое поле сойдёт»: у телеграма `from` приходит пустым, а его id живёт в
# conversation_id — и первый же день, когда он приедет в `from`, превратил бы id 504412830 в
# «+504412830» и склеил бы разных людей в одного. Неизвестный канал сюда не попадает
# намеренно: отсутствие ключа склейки — это одна карточка лишняя, ошибочный ключ — две
# биографии в одной.
_PHONE_CHANNELS = frozenset({"whats-app", "viber", "smsviber"})


def _lead_phone(row: SenderInbound) -> str | None:
    """Телефон лида в той же форме, что и у остальных каналов, или None.

    Форма обязана совпадать с `+62…`, которую пишут Instagram и Evolution: `phone_e164`
    ищется точным равенством, поэтому сырой wa-id `6289689515687` не нашёл бы лида
    `+6289689515687` — и тот же человек завёлся бы второй карточкой вместо того, чтобы
    попасть в общий тред. Ради этого весь узел и существует.
    """
    if (row.channel_name or "") not in _PHONE_CHANNELS:
        return None
    return from_wa_id(row.phone)


class CrmSenderAdapter:
    """ChannelPort поверх sender CRM — все их мессенджеры одним каналом."""

    kind: ChannelKind = ChannelKind.CRM_SENDER

    def __init__(self, session: AsyncSession, mcp: SenderMcp,
                 tenant: SenderTenant, replies_enabled: bool = False) -> None:
        self.session = session
        self.mcp = mcp
        self.tenant = tenant
        self.replies_enabled = replies_enabled

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
                # Их филиал, не любой. Таблица одна на всю установку, а канал принадлежит
                # филиалу: без этого первый же опросивший арендатор забрал бы чужие входящие
                # СЕБЕ — и пометил бы обработанными, так что настоящий адресат не увидел бы
                # их никогда. Пока филиал с sender один, это молчит; вторым оно бы не молчало.
                SenderInbound.branch_ref == self.tenant.branch_id,  # type: ignore[arg-type]
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
                lead_phone=_lead_phone(row),
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
        if not self.replies_enabled:
            return SendResult(ok=False, error=REPLIES_OFF)
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

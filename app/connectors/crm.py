"""CRM WhatsApp — переписка лида, идущая через sender CRM.

Отдельный коннектор, а не режим существующего WhatsApp. Тот работает через Evolution API,
поднятый у нас, и подключается сканированием QR. Здесь чужой транспорт, чужие идентификаторы
проекта и филиала, чужой токен — и отправка, которая отвечает «принято», а не «доставлено».
Общего между ними только название мессенджера.

Что этот коннектор УМЕЕТ: принимать входящие (их приносит колбек) и отвечать в тот же
разговор. Чего НЕ умеет: отзывать сообщения, отмечать прочитанным, забирать профиль или
медиа — всё это операции над аккаунтом, к которому у нас нет доступа. Мы говорим через
посредника.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.crm_whatsapp import CrmWhatsAppAdapter
from app.adapters.db.models import Channel
from app.adapters.sender_mcp import SenderMcp
from app.config import settings
from app.domain.enums import ChannelKind
from app.modules.sender.tenant import SenderTenant
from app.modules.settings.service import get_settings
from app.ports.channel import ChannelPort

from .crm_whatsapp_ui import _ch_crm_wa_form
from .spec import ConnectorSpec, SendWindow

# Пишется в outbox.error и ищется запросами инбокса — переформулировать нельзя.
LATE_ERROR = "crm_wa_window_closed"
DORMANT_REASON = "24-часовое окно WhatsApp закрыто"


async def build_port(session: AsyncSession, channel: Channel) -> ChannelPort:
    """Порт для канала — целиком из настроек ФИЛИАЛА.

    Ни один ключ не берётся из настроек CRM, хотя адрес может совпадать с точностью до
    символа. Это две разные связи: одна про учёт, другая про сам разговор, и отключение
    учёта не должно затыкать переписку с лидом. Совпадение значений — допустимое совпадение,
    не общая настройка.
    """
    cfg = await get_settings(session, channel.branch_id)
    mcp = SenderMcp(cfg.sender_mcp_url, "", timeout_s=settings().sender_mcp_timeout_s)
    tenant = SenderTenant(project=cfg.sender_project,
                          project_id=cfg.sender_project_id,
                          branch_id=cfg.sender_branch_id)
    return CrmWhatsAppAdapter(session, mcp, tenant)


SPEC = ConnectorSpec(
    kind=ChannelKind.CRM_WHATSAPP,
    label="WhatsApp (CRM)",
    label_key="ch.kind_crm_wa",
    icon_class="fa-brands fa-whatsapp",
    icon_color="#128c7e",
    adapter=CrmWhatsAppAdapter,
    build_port=build_port,
    credential_panel=_ch_crm_wa_form,
    settings_prefixes=("sender.",),
    # Ни одной. Не «пока нет»: отзыв, отметка о прочтении, профиль и медиа — действия над
    # аккаунтом WhatsApp, а у нас его нет, мы говорим через чужой сервер.
    capabilities=frozenset(),
    # WhatsApp запрещает свободный текст позже 24 часов от последнего сообщения лида. Вне окна
    # нужен утверждённый шаблон, а это другой вызов с другими параметрами — поэтому обычная
    # отправка честно отказывается, а не уходит в отказ на стороне Meta.
    send_window=SendWindow(error_code=LATE_ERROR, dormant_reason=DORMANT_REASON),
    # Инициировать разговор мы можем — адрес лида известен и живёт в CRM. Ограничение здесь
    # не «некому писать», а «чем писать»: вне окна только шаблоном.
    proactive_outreach=True,
    counts_as_awaiting=True,
    # Канал заводит оператор: у филиала свои project_id и branch_id, и ввести их должен тот,
    # кто получил их от CRM.
    operator_addable=True,
    # Опрос дешёвый: fetch_inbound читает нашу же таблицу, в сеть не ходит. Ограничение
    # частоты здесь ничего не бережёт.
    polls_every_minute=True,
    # Их conversation/send кладёт в очередь и отвечает сразу; настоящий исход приходит позже
    # статусом 1 или 2. Записывать это как «отправлено» значило бы утверждать доставку,
    # которой никто не подтверждал.
    confirms_delivery=False,
)

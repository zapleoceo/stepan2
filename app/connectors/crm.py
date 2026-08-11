"""CRM — переписка лида, идущая через sender CRM, каким бы мессенджером она ни пришла.

Отдельный коннектор, а не режим существующего WhatsApp. Тот работает через Evolution API,
поднятый у нас, и подключается сканированием QR. Здесь чужой транспорт, чужие идентификаторы
проекта и филиала, чужой токен — и отправка, которая отвечает «принято», а не «доставлено».

ОДИН коннектор на все их мессенджеры: у филиала включены whats-app, telegram, viber и
smsviber, и различает их одно поле в полезной нагрузке. Транспорт, учётные данные, адресация
ответа и дедупликация — общие. Разбивать по мессенджерам значило бы копировать коннектор
ради этого поля и заставлять оператора подключать четыре канала там, где связь одна.

Что этот коннектор УМЕЕТ: принимать входящие (их приносит колбек) и отвечать в тот же
разговор. Чего НЕ умеет: отзывать сообщения, отмечать прочитанным, забирать профиль или
медиа — всё это операции над аккаунтом, к которому у нас нет доступа. Мы говорим через
посредника.
"""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.channels.crm import CrmAdapter
from app.adapters.db.models import Channel
from app.adapters.sender_mcp import SenderMcp
from app.config import settings
from app.domain.enums import ChannelKind
from app.modules.sender.tenant import SenderTenant
from app.modules.settings.service import get_settings
from app.ports.channel import ChannelPort

from .crm_ui import _ch_crm_form
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
    return CrmAdapter(session, mcp, tenant, replies_enabled=cfg.sender_enabled)


SPEC = ConnectorSpec(
    kind=ChannelKind.CRM,
    label="CRM (мессенджеры)",
    label_key="ch.kind_crm",
    icon_class="fa-solid fa-comments",
    icon_color="#6366f1",
    adapter=CrmAdapter,
    build_port=build_port,
    credential_panel=_ch_crm_form,
    settings_prefixes=("sender.",),
    # Ни одной. Не «пока нет»: отзыв, отметка о прочтении, профиль и медиа — действия над
    # аккаунтом мессенджера, а у нас его нет, мы говорим через чужой сервер.
    capabilities=frozenset(),
    # WhatsApp запрещает свободный текст позже 24 часов от последнего сообщения лида. Вне окна
    # нужен утверждённый шаблон, а это другой вызов с другими параметрами — поэтому обычная
    # отправка честно отказывается, а не уходит в отказ на стороне Meta.
    #
    # Правило объявлено на весь коннектор, хотя строго оно только вотсаповское: у телеграма
    # такого окна нет. Осознанный перекос в сторону молчания — 99% трафика здесь WhatsApp, а
    # цена ошибки несимметрична: лишний отказ виден в очереди, отправка вне окна ловится
    # блокировкой на стороне Meta. Сделать окно пофайловым можно, когда телеграм наберёт вес.
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

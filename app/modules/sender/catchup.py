"""Добор входящих, которые не довёз колбек.

Ретраев у колбека нет — в их первом документе прямо: `UrlCallback` шлёт с connect_timeout 5с,
«помилки лише логуються». Сообщение, пришедшее в момент нашего перезапуска, пропадает
насовсем: у них оно есть, у нас его не было, и никто об этом не узнает. Инструмент
`sender_inbound_since` — починка: отдаёт входящие за окно, сверяемся по `external_id`, тому же
ключу, по которому дедуплицирует колбек.

Три решения, которые легко потерять при правке.

Окно намеренно перекрывается. Каждый проход перечитывает то, что уже видел: сообщение на
границе окна или расхождение часов на секунды — ровно то, что жалко потерять, а повтор
бесплатен, его гасит уникальный индекс.

Усечённый ответ не игнорируется. `truncated=true` означает, что хвост не поместился; молча
взять что дали значило бы терять сообщения ровно тогда, когда их много. Окно делится пополам
и читается по частям.

Провал не шумит. Добор — страховка, основной путь всё равно колбек, и следующий проход
накроет тот же период. Зато спасённые считаются вслух: колбек, тихо теряющий трафик, не виден
ничем другим.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.sender_mcp import SenderMcp
from app.modules.sender.inbound import CATCHUP, store, to_row
from app.modules.sender.tenant import SenderTenant

logger = logging.getLogger(__name__)

# Глубже дробить окно бессмысленно: 30 минут, поделённые пять раз, это меньше минуты, и если
# и туда не помещается, дело не в ширине окна, а на их стороне.
_MAX_SPLITS = 5


async def _pull(mcp: SenderMcp, args: dict, since: datetime, until: datetime,
                depth: int = 0) -> list[dict]:
    """Входящие за окно, при усечении — по половинам."""
    rows, truncated = await mcp.inbound_since(args, since, until)
    if not truncated or depth >= _MAX_SPLITS:
        if truncated:
            logger.warning("sender catch-up window still truncated after %d splits — "
                           "part of the period was not read", depth)
        return rows
    middle = since + (until - since) / 2
    return (await _pull(mcp, args, since, middle, depth + 1)
            + await _pull(mcp, args, middle, until, depth + 1))


async def sweep(session: AsyncSession, mcp: SenderMcp, tenant: SenderTenant, *,
                now: datetime, lookback_min: int = 30, channel: str = "whats-app") -> int:
    """Перечитать недавнее окно и сохранить то, чего колбек не принёс.

    Возвращает число СПАСЁННЫХ — сообщений, которых у нас иначе не было бы вовсе. Перекрытие
    с прошлым проходом в счёт не идёт: оно норма, а не признак сбоя.
    """
    if not (mcp.configured and tenant.configured):
        return 0
    since = now - timedelta(minutes=lookback_min)
    rescued = 0
    for payload in await _pull(mcp, tenant.inbound_args(channel), since, now):
        row = to_row(payload, arrived_via=CATCHUP)
        if row is None:
            continue
        if await store(session, row):
            rescued += 1
    if rescued:
        # WARNING, а не INFO: каждое такое — лид, чьё сообщение колбек не довёз, и об этом
        # иначе никто бы не узнал.
        logger.warning("sender catch-up rescued %d message(s) the callback never delivered",
                       rescued)
    return rescued

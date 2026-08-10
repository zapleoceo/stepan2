"""Транспорт к MCP-серверу sender: отправка лиду, добор пропущенных, шаблоны.

Это НЕ тот сервер, что отдаёт состояние CRM. Тот (`mcp.itstep.org/mcp/crm`) читает карточки
и пишет события внутрь CRM — клиенту он не пишет вовсе, все 42 его инструмента проверены.
Отправка живёт здесь, за отдельным адресом и отдельным токеном.

Слой тонкий намеренно: здесь только вызовы и разбор конверта, никаких решений о том, что
отправлять. Политика — в адаптере, адресация — в tenant.py, чтобы менять их поодиночке.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.adapters.mcp_client import McpUnavailable, call
from app.adapters.mcp_client import session as mcp_session

logger = logging.getLogger(__name__)

SEND_TOOL = "sender_conversation_send"
INBOUND_TOOL = "sender_inbound_since"
TEMPLATES_TOOL = "sender_list_templates"

# Окно добора sender трактует в киевском времени — не в UTC и не в джакартском (Виктор,
# 05.08.2026). Заголовок X-TimeZone на это не влияет, он про даты в ОТВЕТЕ. Ошибка здесь
# тихая: запрос уйдёт за период, в который никто не писал, вернёт пусто, и это будет
# выглядеть как «ничего не потеряли».
KIEV_OFFSET_H = 3


@dataclass(frozen=True)
class SendOutcome:
    """Что вернула отправка.

    `accepted` — приняли в очередь, НЕ «доставлено клиенту»: их `conversation/send`
    асинхронный и отвечает сразу. Настоящий исход приходит позже статусом 1 или 2, поэтому
    коннектор объявляет confirms_delivery=False и строка ложится в `queued`.
    """

    accepted: bool
    ref: str | None = None
    error: str | None = None


def kiev_window(since: datetime, until: datetime) -> tuple[str, str]:
    """Наивный UTC → строки в киевском времени, как их ждёт sender."""
    shift = timedelta(hours=KIEV_OFFSET_H)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (since + shift).strftime(fmt), (until + shift).strftime(fmt)


def _ref_of(payload: object) -> str | None:
    """Идентификатор, по которому потом найдётся отчёт о доставке.

    Их ответ — ресурс разговора; чем именно назван идентификатор сообщения, в документе не
    сказано, поэтому берём первый из вероятных. Не нашли — не выдумываем: строка останется
    `queued` без ссылки, и это увидит sweep просроченных, а не тихо превратится в «отправлено».
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("external_id", "message_id", "id", "messageId"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


class SenderMcp:
    """Клиент sender. Ничего не решает — только зовёт и разбирает."""

    def __init__(self, url: str, token: str = "", *, timeout_s: float = 30.0) -> None:
        self.url = url
        self.token = token
        self.timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self.url.strip())

    def _target(self) -> str:
        """URL уже несёт токен в query — так его выдаёт CRM и так он хранится в настройках
        филиала. mcp_client переносит токен в заголовок Authorization и вычищает его из
        текста ошибок; повторять эту работу здесь значило бы завести второе место, где
        секрет может утечь в лог."""
        if not self.token:
            return self.url
        joiner = "&" if "?" in self.url else "?"
        return f"{self.url}{joiner}token={self.token}"

    async def send_text(self, args: dict) -> SendOutcome:
        """Отправить текст в разговор. `args` уже собран tenant.send_args()."""
        if not self.configured:
            return SendOutcome(accepted=False, error="sender mcp is not configured")
        try:
            async with mcp_session(self._target(), timeout_s=self.timeout_s) as s:
                payload = await call(s, SEND_TOOL, args)
        except McpUnavailable as exc:
            # Сеть/сервер — повторяемо. Отдаём как ошибку отправки: пусть решает outbox,
            # у него есть и счётчик попыток, и правило «неизвестный исход не повторяем».
            return SendOutcome(accepted=False, error=str(exc)[:200])
        return SendOutcome(accepted=True, ref=_ref_of(payload))

    async def inbound_since(self, tenant_args: dict, since: datetime,
                            until: datetime) -> tuple[list[dict], bool]:
        """Входящие за окно и признак усечения.

        Возвращает (сообщения, truncated). `truncated=True` — окно надо сузить: у них так
        помечен обрезанный ответ, и молча взять первую страницу значило бы потерять хвост
        ровно тогда, когда сообщений много.
        """
        if not self.configured:
            return [], False
        start, end = kiev_window(since, until)
        args = {**tenant_args, "dateStart": start, "dateEnd": end}
        try:
            async with mcp_session(self._target(), timeout_s=self.timeout_s) as s:
                payload = await call(s, INBOUND_TOOL, args)
        except McpUnavailable as exc:
            logger.warning("sender inbound_since failed: %s", str(exc)[:200])
            return [], False
        rows = payload.get("data") if isinstance(payload, dict) else payload
        meta = payload.get("meta") if isinstance(payload, dict) else None
        truncated = bool(meta.get("truncated")) if isinstance(meta, dict) else False
        if not isinstance(rows, list):
            return [], truncated
        return [r for r in rows if isinstance(r, dict)], truncated

    async def templates(self, tenant_args: dict) -> list[dict]:
        """Утверждённые HSM-шаблоны филиала — нужны вне 24-часового окна."""
        if not self.configured:
            return []
        try:
            async with mcp_session(self._target(), timeout_s=self.timeout_s) as s:
                payload = await call(s, TEMPLATES_TOOL, tenant_args)
        except McpUnavailable as exc:
            logger.warning("sender templates failed: %s", str(exc)[:200])
            return []
        rows = payload.get("data") if isinstance(payload, dict) else payload
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

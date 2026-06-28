"""Channel port — uniform interface over Instagram / WhatsApp / Meta Business.

Swapping instagrapi for another API = a new adapter implementing this Protocol; the
domain (funnel, follow-up router) never changes. Reading happens via MBS; follow-up
(window bypass) via private adapters (IG/WA)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.enums import ChannelKind, SessionStatus


@dataclass(frozen=True)
class InboundMessage:
    external_thread_id: str
    sender_id: str
    text: str
    occurred_at: datetime
    product_hint: str | None = None  # из рекламного контекста, если есть


@dataclass(frozen=True)
class SendResult:
    ok: bool
    external_message_id: str | None = None
    error: str | None = None


class ChannelPort(Protocol):
    kind: ChannelKind

    async def fetch_inbound(self) -> list[InboundMessage]:
        """Новые входящие сообщения этого канала (для чтения)."""
        ...

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        """Отправить текст в тред (для ответа/фолоапа)."""
        ...

    async def session_status(self) -> SessionStatus:
        """Жива ли сессия / открыто ли окно ответа."""
        ...

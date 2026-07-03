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
    sender_id: str                       # author of THIS item (own_id on our outgoing)
    text: str
    occurred_at: datetime
    lead_ig_user_id: str | None = None   # thread participant's pk — the lead's stable IG id
    product_hint: str | None = None
    sender_username: str | None = None   # IG @handle
    sender_name: str | None = None       # full name from profile
    sender_avatar: str | None = None     # profile_pic_url (CDN, short-lived)
    ad_id: str | None = None             # Meta Ads Manager ID
    ad_media_id: str | None = None       # IG media ID of the ad creative
    ad_preview_url: str | None = None    # creative thumbnail URL
    lead_source: str | None = None       # 'story'|'ad_clicktomsg'|None
    direction: str = "in"                # 'in' | 'out' (наш ответ из приложения IG)
    external_id: str | None = None       # реальный id сообщения канала (дедуп)
    link_url: str | None = None          # кликабельная цель (шэр ссылки/поста)
    preview_url: str | None = None       # превью карточки (CDN)
    media_url: str | None = None         # in-DM медиа (фото/видео/гиф/голос) для backfill
    media_kind: str | None = None        # image|video|audio
    lead_seen_at: datetime | None = None # read-receipt лида по треду (last_seen_at)


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

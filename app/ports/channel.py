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
    # The number the CHANNEL itself knows, not one the lead typed. On WhatsApp the address
    # IS the phone, so it is identity rather than a guess — and it is the key the whole
    # consolidation joins on. extract_phone(text) stays the fallback for channels where a
    # number only ever appears because someone wrote it out.
    lead_phone: str | None = None
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


@dataclass(frozen=True)
class InboundComment:
    """A comment under one of OUR OWN posts. Public channel, separate from DMs — no thread,
    no 24h window; the author is not (yet) a lead."""
    external_id: str                     # native comment id (dedup key)
    media_id: str                        # the post it sits under
    text: str
    occurred_at: datetime
    author_pk: str | None = None         # numeric IG user id — needed to DM the author
    author_username: str | None = None
    media_caption: str | None = None     # the post's caption — grounding context for a reply
    media_permalink: str | None = None


@dataclass(frozen=True)
class CandidatePost:
    """Somebody ELSE's post, considered as a place to leave a comment.

    Deliberately not shaped like InboundComment: nobody wrote to us, there is no question
    waiting, and the author's only relationship with us is having once sent a DM. The fields
    are what the relevance judge needs in order to decide whether a comment here would be
    welcome or would be us shouting into a stranger's photo album."""

    media_id: str
    author_pk: str
    caption: str
    taken_at: datetime
    author_username: str | None = None
    permalink: str | None = None
    like_count: int = 0
    comment_count: int = 0


class ChannelPort(Protocol):
    """The baseline every connector honours: read, send, and say whether it is alive.

    Nothing optional lives here. It used to also declare fetch_comments/reply_to_comment/
    hide_comment, which only the Instagram adapter implements — so the Protocol described a
    port that two of three adapters were not, and callers had to hasattr() their way back to
    the truth. An optional power is DECLARED per connector in app/connectors/spec.py
    (Capability) and typed where it is consumed — deletions.Revoker, profiles.ProfileFetcher,
    media.MediaDownloader. Only CommentPort lives here, because CommentService takes the whole
    port and calls three of its methods."""

    kind: ChannelKind

    async def fetch_inbound(self) -> list[InboundMessage]:
        """Новые входящие сообщения этого канала (для чтения)."""
        ...

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        """Отправить текст в тред (для ответа/фолоапа)."""
        ...

    async def session_status(self) -> SessionStatus:
        """Жива ли сессия / окно ответа."""
        ...


class CommentPort(ChannelPort, Protocol):
    """Capability.COMMENTS — public comments under our own posts (IG private API)."""

    async def fetch_comments(self, *, since: datetime | None = None) -> list[InboundComment]:
        """Новые комментарии под НАШИМИ постами (public channel)."""
        ...

    async def reply_to_comment(self, comment_external_id: str, text: str) -> SendResult:
        """Публично ответить на комментарий (reply-to-comment по его id)."""
        ...

    async def hide_comment(self, comment_external_id: str) -> SendResult:
        """Удалить спам/оскорбление под нашим постом (private API: delete владельцем)."""
        ...


class OutboundCommentPort(ChannelPort, Protocol):
    """Capability.OUTBOUND_COMMENT — writing under SOMEBODY ELSE'S post.

    Split from CommentPort rather than folded into it because the two carry different risk.
    Answering under our own post is the one automated action every platform tolerates;
    appearing uninvited under a stranger's is the shape anti-spam systems exist to catch. A
    connector may honestly have the first and not the second, and the caps that govern them
    are separate for the same reason."""

    async def fetch_user_posts(self, user_pk: str, *, limit: int = 3) -> list[CandidatePost]:
        """Последние посты ЧУЖОГО аккаунта — кандидаты на комментарий."""
        ...

    async def comment_on_post(self, media_id: str, text: str) -> SendResult:
        """Оставить комментарий верхнего уровня под чужим постом."""
        ...

"""Domain enums — pure, no framework or infra dependencies."""
from __future__ import annotations

from enum import StrEnum


class ChannelKind(StrEnum):
    META_BUSINESS = "meta_business"  # чтение всех сообщений (официальный Graph)
    INSTAGRAM = "instagram"          # фолоап (instagrapi, приватный)
    WHATSAPP = "whatsapp"            # фолоап (Evolution API, приватный)
    WEBSITE = "website"              # чат на сайте: синхронный HTTP, обратного канала нет


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"      # платформенный: создаёт филиалы, видит всё
    BRANCH_ADMIN = "branch_admin"    # полный доступ в своём филиале
    BRANCH_VIEWER = "branch_viewer"  # только чтение в своём филиале


class Stage(StrEnum):
    """Declared in funnel order, then side states. Nothing in the code compares members by
    this order (only equality/membership), so this carries no functional weight — it exists
    for whoever reads this next.

    A funnel step is something every won lead passes through once, in order. A side state is
    something that can happen at any step and hand the lead back to any step. Two members were
    moved out of the line after measuring where leads actually come from and go to:

    NURTURING (2026-07-23): 84% arrive from an already-active stage going quiet, not from NEW.
    OBJECTION (2026-07-25): over 30 days it was entered from qualifying (39%), presenting
    (24%), new (14%) and even ready — and left back to qualifying, presenting and ready. It is
    a thing the lead does, not a place they reach. Kept in the line it made the funnel read as
    if every buyer must doubt between presenting and ready, and turned an ordinary moment of
    the conversation into an apparent bottleneck in the report."""

    NEW = "new"
    QUALIFYING = "qualifying"
    PRESENTING = "presenting"
    READY = "ready"
    HANDED_OFF = "handed_off"
    OBJECTION = "objection"
    NURTURING = "nurturing"
    DORMANT = "dormant"
    MANAGER = "manager"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"      # окно/сессия истекли (напр. MBS 24ч)
    CHALLENGE = "challenge"  # требуется ре-логин/верификация
    # QR показан, телефон ещё не подтвердил. Намеренно НЕ active: пока привязки нет,
    # воркер не должен получить порт на этот канал (см. active_session_settings).
    PENDING = "pending"


# Стадии, в которых бот молчит безусловно. `manager` сюда НЕ входит (S1-семантика):
# молчание там задаёт per-lead agent_enabled, менеджер может вернуть бота под надзором.
BOT_SILENT_STAGES: frozenset[Stage] = frozenset(
    {Stage.READY, Stage.HANDED_OFF, Stage.DORMANT}
)

# Стадии, где ведёт человек — свежий входящий НЕ включает бота обратно
# (dormant сюда не входит: спящий лид оживает в qualifying).
HUMAN_LED_STAGES: frozenset[Stage] = frozenset(
    {Stage.READY, Stage.HANDED_OFF, Stage.MANAGER}
)

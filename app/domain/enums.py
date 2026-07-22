"""Domain enums — pure, no framework or infra dependencies."""
from __future__ import annotations

from enum import StrEnum


class ChannelKind(StrEnum):
    META_BUSINESS = "meta_business"  # чтение всех сообщений (официальный Graph)
    INSTAGRAM = "instagram"          # фолоап (instagrapi, приватный)
    WHATSAPP = "whatsapp"            # фолоап (Evolution API, приватный)


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"      # платформенный: создаёт филиалы, видит всё
    BRANCH_ADMIN = "branch_admin"    # полный доступ в своём филиале
    BRANCH_VIEWER = "branch_viewer"  # только чтение в своём филиале


class Stage(StrEnum):
    """Declared in funnel order, then side states. Nothing in the code compares members by
    this order (only equality/membership), so this carries no functional weight — it exists
    for whoever reads this next. NURTURING sits with the side states on purpose: measured
    live (2026-07-23), 84% of leads that enter it come from an already-active stage going
    quiet, not from NEW — it's a state entered from (and returned to) any active stage, not
    a step in the sequence, exactly like DORMANT."""

    NEW = "new"
    QUALIFYING = "qualifying"
    PRESENTING = "presenting"
    OBJECTION = "objection"
    READY = "ready"
    HANDED_OFF = "handed_off"
    NURTURING = "nurturing"
    DORMANT = "dormant"
    MANAGER = "manager"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"      # окно/сессия истекли (напр. MBS 24ч)
    CHALLENGE = "challenge"  # требуется ре-логин/верификация


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

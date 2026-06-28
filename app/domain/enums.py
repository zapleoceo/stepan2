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
    NEW = "new"
    NURTURING = "nurturing"
    QUALIFYING = "qualifying"
    PRESENTING = "presenting"
    OBJECTION = "objection"
    READY = "ready"
    HANDED_OFF = "handed_off"
    DORMANT = "dormant"
    MANAGER = "manager"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"      # окно/сессия истекли (напр. MBS 24ч)
    CHALLENGE = "challenge"  # требуется ре-логин/верификация


# Стадии, в которых бот молчит (ведёт человек / завершено).
BOT_SILENT_STAGES: frozenset[Stage] = frozenset(
    {Stage.READY, Stage.HANDED_OFF, Stage.DORMANT, Stage.MANAGER}
)

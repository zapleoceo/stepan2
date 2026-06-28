"""SQLModel tables — core multi-tenant schema.

Every tenant-owned row carries `branch_id`; isolation is enforced in the repository
layer (a single scoped() helper), never ad-hoc per query. No business logic here.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

from app.domain.enums import ChannelKind, Role, SessionStatus, Stage


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Branch(SQLModel, table=True):
    """Арендатор. Дефолтного филиала нет — у каждого свой id."""
    __tablename__ = "branch"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    lang: str = Field(default="id", description="язык общения филиала")
    tz_offset_h: int = Field(default=7)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)


class Channel(SQLModel, table=True):
    """Аккаунт канала филиала (MBS/IG/WA)."""
    __tablename__ = "channel"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    kind: ChannelKind
    handle: str | None = Field(default=None, description="username / номер / page handle")
    account_id: str | None = Field(default=None, description="внешний id аккаунта")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)


class ChannelSession(SQLModel, table=True):
    """Живая сессия канала. secret_enc — Fernet-шифрованная (никогда в открытом виде)."""
    __tablename__ = "channel_session"

    id: int | None = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channel.id", index=True)
    secret_enc: str
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)
    expires_at: datetime | None = Field(default=None, description="окно/сессия (напр. MBS 24ч)")
    last_ok_at: datetime | None = Field(default=None)


class Lead(SQLModel, table=True):
    """Личность лида в филиале. Сопоставление между каналами — по phone_e164."""
    __tablename__ = "lead"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    display_name: str | None = Field(default=None)
    phone_e164: str | None = Field(default=None, index=True, description="ключ сопоставления")
    email: str | None = Field(default=None)
    stage: Stage = Field(default=Stage.NEW)
    ready_subtype: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class ChannelThread(SQLModel, table=True):
    """Тред лида в конкретном канале (= чат). Несколько тредов на лида = чаты по
    разным продуктам/каналам; фолоап через другой канал НЕ плодит лида."""
    __tablename__ = "channel_thread"

    id: int | None = Field(default=None, primary_key=True)
    lead_id: int = Field(foreign_key="lead.id", index=True)
    channel_id: int = Field(foreign_key="channel.id", index=True)
    external_thread_id: str = Field(index=True, description="ig_thread / wa-jid / mbs convo id")
    product_slug: str | None = Field(default=None)
    window_until: datetime | None = Field(default=None, description="окно ответа канала")
    last_in_at: datetime | None = Field(default=None)
    last_out_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class User(SQLModel, table=True):
    """Пользователь платформы (вход по Telegram)."""
    __tablename__ = "app_user"

    id: int | None = Field(default=None, primary_key=True)
    telegram_id: int = Field(unique=True, index=True)
    name: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class Membership(SQLModel, table=True):
    """Роль пользователя в филиале. branch_id=NULL → super_admin (вся платформа)."""
    __tablename__ = "membership"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="app_user.id", index=True)
    branch_id: int | None = Field(default=None, foreign_key="branch.id", index=True)
    role: Role

"""SQLModel tables — core multi-tenant schema.

Every tenant-owned row carries `branch_id`; isolation is enforced in the repository
layer (a single scoped() helper), never ad-hoc per query. No business logic here.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import BigInteger, LargeBinary, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.domain.enums import ChannelKind, Role, SessionStatus, Stage


def _utcnow() -> datetime:
    # naive UTC — совпадает с колонками TIMESTAMP WITHOUT TIME ZONE (Postgres строг к этому)
    return datetime.now(UTC).replace(tzinfo=None)


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
    kind: ChannelKind = Field(sa_type=String)  # хранится как VARCHAR (StrEnum value)
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
    status: SessionStatus = Field(default=SessionStatus.ACTIVE, sa_type=String)
    expires_at: datetime | None = Field(default=None, description="окно/сессия (напр. MBS 24ч)")
    last_ok_at: datetime | None = Field(default=None)


class Lead(SQLModel, table=True):
    """Личность лида в филиале. Сопоставление между каналами — по phone_e164."""
    __tablename__ = "lead"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    display_name: str | None = Field(default=None)
    ig_username: str | None = Field(default=None, index=True)
    ig_user_id: str | None = Field(default=None, index=True)
    avatar_url: str | None = Field(default=None)
    phone_e164: str | None = Field(default=None, index=True, description="ключ сопоставления")
    email: str | None = Field(default=None)
    stage: Stage = Field(default=Stage.NEW, sa_type=String)
    ready_subtype: str | None = Field(default=None)
    agent_enabled: bool = Field(default=True, description="per-lead бот-тумблер (manager takeover)")
    is_blocked: bool = Field(default=False, index=True, description="спам/бан — бот игнорит")
    handed_off_at: datetime | None = Field(default=None)
    follower_count: int | None = Field(default=None)
    following_count: int | None = Field(default=None)
    last_active_at: datetime | None = Field(default=None)
    profile_synced_at: datetime | None = Field(default=None, description="последний refresh")
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
    next_followup_at: datetime | None = Field(default=None, description="время следующего фолоапа")
    followups_sent: int = Field(default=0, description="сколько фолоапов уже ушло по расписанию")
    context_cleared_at: datetime | None = Field(
        default=None, description="watermark: диалог до этого времени не идёт в промпт")
    lead_source: str | None = Field(default=None, description="story|ad_clicktomsg|None")
    ad_id: str | None = Field(default=None, description="Meta Ads Manager ID")
    ad_media_id: str | None = Field(default=None, description="IG media ID of ad creative")
    ad_preview_url: str | None = Field(default=None, description="ad creative thumbnail CDN URL")
    created_at: datetime = Field(default_factory=_utcnow)


class User(SQLModel, table=True):
    """Пользователь платформы (вход по Telegram)."""
    __tablename__ = "app_user"

    id: int | None = Field(default=None, primary_key=True)
    # Telegram IDs exceed int32 range — must be BIGINT
    telegram_id: int = Field(unique=True, index=True, sa_type=BigInteger)
    name: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class Membership(SQLModel, table=True):
    """Роль пользователя в филиале. branch_id=NULL → super_admin (вся платформа)."""
    __tablename__ = "membership"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="app_user.id", index=True)
    branch_id: int | None = Field(default=None, foreign_key="branch.id", index=True)
    role: Role = Field(sa_type=String)


# ─── доменные таблицы филиала (все несут branch_id; изоляция — в репозитории) ───

class KnowledgeDoc(SQLModel, table=True):
    """Документ базы знаний филиала (persona / faq / market_facts / stories)."""
    __tablename__ = "knowledge_doc"
    __table_args__ = (UniqueConstraint("branch_id", "slug", name="uq_kdoc_branch_slug"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    slug: str
    title: str | None = Field(default=None)
    content: str = Field(default="")


class Product(SQLModel, table=True):
    """Карточка продукта/курса филиала — единственный источник цены/деталей в ответах."""
    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("branch_id", "slug", name="uq_product_branch_slug"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    slug: str
    title: str
    content: str = Field(default="")
    is_active: bool = Field(default=True)
    sort_order: int = Field(default=0)


class AppSetting(SQLModel, table=True):
    """Настройка. branch_id=NULL → платформенная; иначе настройка филиала."""
    __tablename__ = "app_setting"
    __table_args__ = (UniqueConstraint("branch_id", "key", name="uq_setting_branch_key"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int | None = Field(default=None, foreign_key="branch.id", index=True)
    key: str
    value: str = Field(default="")


class Message(SQLModel, table=True):
    """Сообщение в треде. branch_id денормализован для быстрых выборок воркера."""
    __tablename__ = "message"
    __table_args__ = (UniqueConstraint("channel_id", "external_id", name="uq_msg_ext"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    thread_id: int = Field(foreign_key="channel_thread.id", index=True)
    channel_id: int = Field(foreign_key="channel.id")
    external_id: str
    direction: str = Field(description="in|out")
    sent_by: str = Field(default="lead", description="lead|agent|manager")
    text: str = Field(default="")
    occurred_at: datetime = Field(default_factory=_utcnow)
    llm_info: str | None = Field(default=None)
    tr_text: str | None = Field(default=None, description="кэш перевода (не биллить повторно)")
    delete_requested: bool = Field(default=False, index=True, description="ждёт IG-unsend")
    media_pending: bool = Field(default=False, index=True, description="медиа ждёт backfill")


class Outbox(SQLModel, table=True):
    """Единственный исходящий путь — очередь на отправку (caps/окна применяются раз)."""
    __tablename__ = "outbox"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    thread_id: int = Field(foreign_key="channel_thread.id", index=True)
    text: str
    source: str = Field(default="agent", description="agent|manager|followup")
    status: str = Field(default="pending", index=True, description="pending|sent|failed")
    scheduled_at: datetime = Field(default_factory=_utcnow)
    sent_at: datetime | None = Field(default=None)
    error: str | None = Field(default=None)
    llm_info: str | None = Field(default=None)


class ManagerAlert(SQLModel, table=True):
    """Событие передачи лида менеджеру — источник для уведомления и CRM."""
    __tablename__ = "manager_alert"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    lead_id: int = Field(foreign_key="lead.id", index=True)
    thread_id: int | None = Field(default=None, foreign_key="channel_thread.id")
    kind: str = Field(description="ready_deal|ready_openhouse|needs_manager")
    actor: str = Field(default="auto")
    lead_phone: str | None = Field(default=None)
    summary_en: str = Field(default="")
    summary_ru: str = Field(default="")
    synced_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class CoachingEdit(SQLModel, table=True):
    """Coach KB editor: manager request → LLM proposes old→new diff → manager applies."""
    __tablename__ = "coaching_edit"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    request: str
    status: str = Field(default="proposed")  # proposed|applied|cancelled|clarify|failed
    slug: str | None = Field(default=None, description="target knowledge doc slug")
    old_text: str | None = Field(default=None)
    new_text: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    added_by: str | None = Field(default=None)
    applied_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class CoachingNote(SQLModel, table=True):
    """Директива для бота: менеджер в чате задаёт правила/советы.

    role=manager + active=True инжектируются в промпт как обязательные правила.
    role=stepan — реплики-подтверждения (только для истории, не в промпте).
    """
    __tablename__ = "coaching_note"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    role: str = Field(description="manager|stepan")
    text: str
    active: bool = Field(default=True)
    added_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class StageEvent(SQLModel, table=True):
    """Журнал переходов воронки — кто/когда/почему сменил стадию лида."""
    __tablename__ = "stage_event"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    lead_id: int = Field(foreign_key="lead.id", index=True)
    thread_id: int | None = Field(default=None)
    from_stage: str
    to_stage: str
    actor: str = Field(default="bot", description="bot|manager|system|<user name>")
    reason: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow)


class MediaAsset(SQLModel, table=True):
    """Скачанное медиа лида (IG image/video/audio) — заполняется backfill-воркером."""
    __tablename__ = "media_asset"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    message_id: int | None = Field(default=None, foreign_key="message.id", index=True)
    kind: str = Field(description="image|video|audio")
    mime: str | None = Field(default=None)
    url: str | None = Field(default=None, description="CDN-ссылка источника (может протухнуть)")
    data: bytes | None = Field(default=None, sa_type=LargeBinary)
    created_at: datetime = Field(default_factory=_utcnow)


class LlmSpend(SQLModel, table=True):
    """Дневной LLM-расход филиала (cost_usd от брокера) — основа budget-гейта."""
    __tablename__ = "llm_spend"
    __table_args__ = (UniqueConstraint("branch_id", "day", name="uq_llm_spend_branch_day"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    day: date = Field(index=True, description="локальный день филиала (tz_offset_h)")
    used_usd: float = Field(default=0.0)
    calls: int = Field(default=0)

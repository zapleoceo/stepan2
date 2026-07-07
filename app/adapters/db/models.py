"""SQLModel tables — core multi-tenant schema.

Every tenant-owned row carries `branch_id`; isolation is enforced in the repository
layer (a single scoped() helper), never ad-hoc per query. No business logic here.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, LargeBinary, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.domain.clock import utc_now as _utcnow
from app.domain.enums import ChannelKind, Role, SessionStatus, Stage


class Branch(SQLModel, table=True):
    """Арендатор. Дефолтного филиала нет — у каждого свой id."""
    __tablename__ = "branch"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    lang: str = Field(default="id", description="язык общения филиала")
    tz_offset_h: int = Field(default=7)
    is_active: bool = Field(default=True)
    # When set, this branch READS its knowledge base (persona/products/docs/RAG) from
    # another branch instead of its own — one shared source of truth. Chats/leads stay own.
    kb_source_branch_id: int | None = Field(default=None, foreign_key="branch.id")
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
    lead_type: str | None = Field(
        default=None, description="intent segment: hot|warm|cold|no_budget|non_target")
    audience: str | None = Field(
        default=None, description="who the lead is (orthogonal to intent): adult|student")
    preferred_language: str | None = Field(
        default=None, description="если лид попросил другой язык — отвечаем на нём")
    needs: str | None = Field(
        default=None, description="JSON-профиль потребности: jobs/pains/gains + discovery_complete")
    needs_tr: str | None = Field(
        default=None,
        description="кэш перевода needs: {lang: {ориг.фраза: перевод}} — не биллить повторно")
    agent_enabled: bool = Field(default=True, description="per-lead бот-тумблер (manager takeover)")
    is_blocked: bool = Field(default=False, index=True, description="спам/бан — бот игнорит")
    handed_off_at: datetime | None = Field(default=None)
    follower_count: int | None = Field(default=None)
    following_count: int | None = Field(default=None)
    last_active_at: datetime | None = Field(default=None)
    profile_synced_at: datetime | None = Field(default=None, description="последний refresh")
    notify_topic_id: int | None = Field(
        default=None, description="Telegram forum topic (message_thread_id) для алертов лида")
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
    product_source: str | None = Field(
        default=None, description="ad|model|manager — origin of product_slug; gates override")
    window_until: datetime | None = Field(default=None, description="окно ответа канала")
    last_in_at: datetime | None = Field(default=None)
    last_out_at: datetime | None = Field(default=None)
    lead_seen_at: datetime | None = Field(
        default=None, description="read-receipt лида (IG last_seen_at)")
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
    """Документ базы знаний филиала (persona / faq / playbook / …). category группирует
    их в дерево UI; content — markdown с `## ` секциями (границы чанков для RAG)."""
    __tablename__ = "knowledge_doc"
    __table_args__ = (UniqueConstraint("branch_id", "slug", name="uq_kdoc_branch_slug"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    slug: str
    title: str | None = Field(default=None)
    category: str | None = Field(default=None, description="группа дерева: persona/playbook/…")
    sort_order: int = Field(default=0)
    content: str = Field(default="")
    # onupdate bumps this on ANY ORM edit so the RAG watcher (branch_needs_reindex) detects
    # the change and rebuilds — without it, a content edit left the index stale on old text.
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
    updated_by: str | None = Field(default=None, description="кто правил (owner id)")


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
    kind: str = Field(default="course", description="course | event — event = RSVP not enrolment")
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
    updated_by: str | None = Field(default=None)


class AdProductMap(SQLModel, table=True):
    """Оператор-заданное соответствие рекламное объявление (ad_id) → продукт. При первом
    входящем из этой рекламы тред получает product_slug как стартовый (product_source='ad');
    Степан может переквалифицировать позже — уверенное решение модели перебивает ad-дефолт,
    но не ручной выбор менеджера."""
    __tablename__ = "ad_product_map"
    __table_args__ = (UniqueConstraint("branch_id", "ad_id", name="uq_admap_branch_ad"),)

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    ad_id: str = Field(index=True)
    product_slug: str
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
    updated_by: str | None = Field(default=None)


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
    link_url: str | None = Field(default=None, description="кликабельная цель (шэр поста/ссылки)")
    preview_url: str | None = Field(default=None, description="превью карточки (CDN, протухает)")


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
    tr_text: str | None = Field(default=None, description="кэш перевода очередной реплики")
    attempts: int = Field(default=0, description="soft-block retries so far — capped, not "
                                                  "infinite (see outbox._MAX_SOFT_BLOCK_ATTEMPTS)")


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


class McpToken(SQLModel, table=True):
    """A bearer token issued to an MCP client. Only the SHA-256 hash is stored (shown once
    at creation, never again); `prefix` is the first chars for identification in the UI.
    `scope` = write (funnel ops) | read (read-only reader). Platform-level, not per-branch:
    a token grants API access across branches (the reader tool filters by branch itself)."""
    __tablename__ = "mcp_token"

    id: int | None = Field(default=None, primary_key=True)
    label: str = Field(description="who this token is for, e.g. 'director' / 'partner CRM'")
    scope: str = Field(description="write | read")
    token_hash: str = Field(index=True, unique=True, description="sha256 hex of the token")
    prefix: str = Field(description="first chars of the token, for identification only")
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)


class CrmLeadState(SQLModel, table=True):
    """Cached CRM state for a lead — the READ side of the CRM link. Populated by the pull
    sync and by the point-check right before a send. The send-gate reads `verdict` to
    decide whether Stepan may still contact the lead (proceed) or must stand down (hold,
    e.g. a manager owns it / deal closed). One row per lead, upserted."""
    __tablename__ = "crm_lead_state"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    lead_id: int = Field(foreign_key="lead.id", index=True, unique=True)
    exists_in_crm: bool = Field(default=False)
    status: str | None = Field(default=None, description="raw CRM status of the lead/client")
    owner: str | None = Field(default=None, description="bot|manager|none")
    verdict: str = Field(default="proceed", description="proceed|hold")
    reason: str | None = Field(default=None)
    raw: str | None = Field(default=None, description="verbatim CRM JSON for display/debug")
    fetched_at: datetime = Field(default_factory=_utcnow, index=True)


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


class ThreadLog(SQLModel, table=True):
    """Технический лог треда для отображения в окне чата (не воронка — см. StageEvent):
    очистка/загрузка контекста и подобные действия менеджера над самим чатом."""
    __tablename__ = "thread_log"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    thread_id: int = Field(foreign_key="channel_thread.id", index=True)
    kind: str = Field(description="context_cleared|context_loaded")
    detail: str | None = Field(default=None)
    actor: str = Field(default="manager")
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


class BrokerLog(SQLModel, table=True):
    """Одна строка на КАЖДЫЙ вызов брокера (reply/translate/embed/suggest). Точка записи
    одна — BrokerLLM: у брокера есть цена/латентность/провайдер, но нет thread_id и типа
    вызова — их знает только Степан. Смотрится на /settings/log. 30-дневная ретенция."""
    __tablename__ = "broker_log"

    id: int | None = Field(default=None, primary_key=True)
    request_id: str | None = Field(default=None, description="broker request_id — для сверки")
    branch_id: int | None = Field(default=None, index=True)
    thread_id: int | None = Field(default=None)
    kind: str | None = Field(default=None, description="workflow: reply/translate/embed/…")
    capability: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    model: str | None = Field(default=None)
    tokens_in: int = Field(default=0)
    tokens_out: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    latency_ms: int | None = Field(default=None)
    ok: bool = Field(default=True)
    error: str | None = Field(default=None, description="сообщение ошибки при ok=false")
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class KnowledgeChunk(SQLModel, table=True):
    """Кусок базы знаний с эмбеддингом для RAG-поиска. Пересобирается целиком при
    переиндексации (DELETE+INSERT, без UPDATE). embedding — JSON-массив float (cosine
    в Python, чтобы работать и на SQLite-тестах, и на Postgres-проде)."""
    __tablename__ = "knowledge_chunk"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int = Field(foreign_key="branch.id", index=True)
    source_type: str = Field(default="doc", description="doc | product")
    source_slug: str = Field(default="")
    title: str = Field(default="")
    seq: int = Field(default=0)
    text: str = Field(default="")
    embedding: str = Field(default="[]", description="JSON-массив float (Voyage-вектор)")
    created_at: datetime = Field(default_factory=_utcnow)


class KnowledgeRevision(SQLModel, table=True):
    """История правок базы знаний (doc + product). Пишется триггером БД на любое изменение
    content — UI, восстановление, seed. Ничего не теряется; любую версию видно и можно
    восстановить. actor = updated_by источника."""
    __tablename__ = "knowledge_revision"

    id: int | None = Field(default=None, primary_key=True)
    branch_id: int | None = Field(default=None, index=True)
    entity_type: str = Field(default="doc", description="doc | product")
    slug: str = Field(default="")
    old_content: str | None = Field(default=None)
    new_content: str = Field(default="")
    old_len: int | None = Field(default=None)
    new_len: int = Field(default=0)
    actor: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, index=True)

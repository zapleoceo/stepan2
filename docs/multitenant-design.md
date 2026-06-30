# Stepan-2 — архитектура мультиарендности

Статус: **в разработке** (Фаза 0). Решения зафиксированы (см. ниже). Воркер на старте
выключен — не конфликтуем со Stepan-1.

## Зафиксированные решения

1. **Мультиарендность — общая БД + воркер-на-филиал.** Одна БД и одна админка
   (super-admin видит все филиалы, `branch_id` изолирует), отдельный воркер-процесс на
   филиал (владеет сессиями каналов; изоляция для анти-бана).
2. **Дефолтного филиала нет** — у каждого свой `branch_id`.
3. **LLM только через брокер (AIbroker).** Локальный пул ключей провайдеров полностью
   выпилен. Бюджет/учёт — по `cost_usd`, который брокер возвращает в каждом ответе;
   бюджет на филиал.
4. **Каналы за абстракцией `ChannelAdapter`:**
   - **Чтение всех сообщений** — Meta Business Suite (официальный Graph + webhook;
     требует App Review — срок на стороне Meta).
   - **Фолоап (обход 24ч-окна MBS):** Instagram (instagrapi, приватный) и WhatsApp
     (Evolution API, self-hosted, приватный). Messenger — позже.
5. **Единый чат лида.** Фолоап через приватный канал **не плодит** новый чат — остаётся
   единым. Сопоставление лида между каналами — по нормализованному телефону
   (`extract_phone_intl`).
6. **Чаты по продуктам.** У одного лида может быть несколько чатов (разные продукты) —
   в сайдбаре видно и можно открыть каждый.
7. **Изоляция** отдельный репо/БД/контейнер от Stepan-1.

## Технологический стек (фреймворки, не самопис)

| Слой | Фреймворк | Зачем |
|---|---|---|
| Web/API | **FastAPI** | async, OpenAPI, DI через Depends |
| ORM/модели | **SQLModel** | Pydantic + SQLAlchemy, типизация, меньше boilerplate |
| Миграции | **Alembic** | версионирование, autogenerate (не самописный SQL) |
| Конфиг | **Pydantic Settings** | типобезопасный конфиг из env |
| Auth + RBAC | **FastAPI-Users** + **Casbin** | вход + политики ролей/филиалов |
| Фоновые задачи | **ARQ** (Redis) | очередь задач вместо самописных async-циклов |
| Админка | **SQLAdmin** + главный UI | SQLAdmin — low-level backup; основной CRUD филиалов/KB/продуктов — в `/ui/` |
| Каналы / LLM | **Port + Adapter** | заменяемость API без переписывания ядра |
| Тесты | **pytest** + pytest-asyncio + **testcontainers** | реальный Postgres/Redis в тестах |

## Структура (модульный монолит, Hexagonal / Ports & Adapters)

Ядро не зависит от фреймворков и внешних API. Всё внешнее — за портом; реализация —
адаптер. Заменить instagrapi/Evolution/брокер = заменить адаптер, ядро не трогаем.

```
app/
  domain/        чистая логика: воронка, политики фолоапа, identity-резолвинг
  ports/         интерфейсы: ChannelPort, LLMPort, Repository, NotifierPort, BudgetPort
  adapters/
    db/          SQLModel-репозитории (реализация Repository)
    llm/         broker-адаптер (реализация LLMPort)
    channels/    instagram/ whatsapp/ meta_business/ (реализации ChannelPort)
    notify/      telegram-адаптер (NotifierPort)
  modules/       bounded contexts: branches, channels, leads, knowledge, auth, notifications
  api/           FastAPI-роутеры (тонкие: валидация → use-case)
  admin/         SQLAdmin-панель
  worker/        ARQ-задачи (ingest/reply/outbox/followup) — на филиал
  config.py      Pydantic Settings
migrations/      Alembic
tests/
```

Каждый модуль — свой bounded context с domain/ports/adapters; зависимости только внутрь
(на domain), наружу — через порты. Это и есть «поменять кусок, не перепиливая всё».

## Модель данных (core)

- **`branch`** — арендатор: `id`, `name`, `lang`, `tz_offset_h`, `is_active`.
- **`channel`** — аккаунт канала филиала: `branch_id`, `kind`
  (`meta_business`|`instagram`|`whatsapp`), `handle`/`account_id`, `is_active`.
- **`channel_session`** — живая сессия канала: `channel_id`, `secret_enc` (Fernet),
  `status`, `expires_at`/`window_until`, `last_ok_at`.
- **`lead`** — личность лида в филиале: `branch_id`, `display_name`, `phone_e164`
  (ключ сопоставления), `email`, `stage`, `ready_subtype`.
- **`channel_thread`** — тред лида в канале: `lead_id`, `channel_id`,
  `external_thread_id`, `product_slug`, `window_until`, `last_in_at`/`last_out_at`.
  Несколько тредов на лида = чаты по разным продуктам/каналам.
- Доменные таблицы (знания/продукты/настройки/алерты) получают `branch_id`.
- **RBAC:** `user` (telegram_id, name) + `membership` (user_id, branch_id, role):
  `super_admin` (branch_id NULL) / `branch_admin` / `branch_viewer`.

## Каналы и фолоап-роутинг

`ChannelAdapter` (Strategy): `fetch_threads`, `send_text`, `send_media`, `session_status`.
Реализации: `MetaBusinessAdapter` (чтение), `InstagramAdapter` (instagrapi),
`WhatsAppAdapter` (Evolution API HTTP).

Выбор канала для фолоапа:
1. Окно открыто (≤24ч) — пишем туда, где лид был активен последним.
2. Окно закрыто — приватный канал (WhatsApp Evolution / Instagram) — обход окна.
3. Нет доступного приватного канала — лид в `dormant` с причиной.

Честное ограничение: фолоап через приватный канал **не появится** в чате MBS (разные
каналы внутри Meta), но единая личность `lead` агрегирует все треды у нас.

## Миграция и старт

- **Первый филиал — «Индонезия».** Стягиваем из Stepan-1 только **знания + настройки**
  (knowledge_doc, products, persona, market_facts, stories, ad_map, settings). Чаты не
  переносим — накопятся заново.
- **Воркер выключен на старте** (не слать в IG параллельно со Stepan-1 на том же
  аккаунте → конфликт сессий/бан). Включаем по команде после проверки.
- Stepan-1 не трогаем; полностью независимый стек.

## Фазы

0. Фундамент репо (структура, core-модели, broker-only, infra, CI, docs). ← сейчас
1. Branch-aware ядро + изоляция + миграция знаний Индонезии.
2. Абстракция каналов (MBS чтение, IG, WhatsApp Evolution).
3. Личность лида + роутер фолоапа + чаты по продуктам.
4. RBAC + super-admin UI.
5. Деплой + тесты + проверка (воркер выключен) → готовность к старту.

## Принципы реализации

- **DI вместо синглтонов:** `branch_id`/контекст филиала передаётся явно (никаких
  глобальных `_session`/`get_settings`).
- **Единая `scoped()`-обёртка** фильтрации по филиалу — DRY, не пишем `branch_id` руками
  в каждом запросе.
- Тесты на каждую фазу: изоляция филиалов, роутинг фолоапа, сопоставление лида, RBAC.
- Документация обновляется в том же PR.

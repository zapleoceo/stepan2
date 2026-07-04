# Stepan-2 — архитектура мультиарендности

Статус: **реализовано и в проде** (все фазы 0–5 закрыты). Решения зафиксированы (см.
ниже). Актуальное состояние запуска — [launch-checklist.md](launch-checklist.md).

## Зафиксированные решения

1. **Мультиарендность — общая БД + общий воркер.** Одна БД и одна админка
   (super-admin видит все филиалы, `branch_id` изолирует). Фактически работает **один
   общий ARQ-воркер** (`stepan2-worker`): краны итерируют активные филиалы; изоляция —
   `branch_id` в данных + advisory-lock на тред (см. [worker.md](worker.md)).
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
| Auth + RBAC | кастомный **Telegram Login** (HMAC-верификация) + session-cookie middleware (`app/api/_auth.py`) | вход; RBAC — собственная grant-таблица (`app/modules/auth/rbac.py`) |
| Фоновые задачи | **ARQ** (Redis) | очередь задач вместо самописных async-циклов |
| Админка | **SQLAdmin** + главный UI | SQLAdmin — low-level backup; основной CRUD филиалов/KB/продуктов — в `/ui/` |
| Каналы / LLM | **Port + Adapter** | заменяемость API без переписывания ядра |
| Тесты | **pytest** + pytest-asyncio + in-memory **SQLite** (`tests/conftest.py`) | testcontainers объявлен в dev-deps, но не используется |

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
- **RBAC:** `app_user` (telegram_id, name) + `membership` (user_id, branch_id, role):
  `super_admin` (branch_id NULL) / `branch_admin` / `branch_viewer`.

## Контроль доступа (enforcement)

Модель ролей в `app/domain/enums.py` (`Role`); хранение — в `membership`.
`super_admin` имеет `branch_id = NULL` (вся платформа), остальные привязаны к одному
филиалу. Принуждение — в `app/admin/_branch.py`:

- `branch_ids_from_request(request)` — **view-фильтр**: пересекает cookie
  `stepan2_branch` с `request.state.allowed_branch_ids`. `None` = super_admin (видит всё).
  **Инвариант безопасности:** scoped-пользователь не может выйти за пределы своих
  филиалов, подделав cookie — сервер всегда пересекает с `allowed_branch_ids`.
- `allowed_branch_ids(request)` — **право действия** (write/manage), игнорирует cookie.
- `is_branch_forbidden(branch_id, allowed)` — per-row tenant-guard: `True`, если строка
  чужого филиала. Применяется во всех chat/knowledge/product/channel/coach роутах перед
  чтением/записью (защита от IDOR).
- `is_super_admin(request)` / `require_super_admin(request)` — FastAPI-зависимость,
  отдаёт 403 всем, кроме super_admin.
- `writable_branch_ids(request)` / `is_branch_write_forbidden(branch_id, writable)` —
  право **записи** по филиалам. `None` = пишет везде (super_admin); `[]` = только чтение
  (branch_viewer); `[id…]` = branch_admin своих филиалов.

### Роли на запись: `branch_admin` vs `branch_viewer`

Грант-таблица `app/modules/auth/rbac.py` (`Action` × `Role`, `can(role, action)`) —
единственный источник «кто что может». На логине из membership'ов считается claim `wr`
(филиалы, где роль даёт `Action.WRITE`) и кладётся в подписанную сессию рядом с `br`
(чтение) и `sa` (super).

Принуждение централизовано в `WriteGuardMiddleware`: любой изменяющий `POST /ui/**`
блокируется (403) для сессии без права записи (`sa=False`, `wr=[]` → branch_viewer). Так
ни один из ~30 write-роутов нельзя забыть закрыть. Исключение — «read-support» POST'ы,
которые нужны для **чтения** и ничего не мутируют (см. `_is_read_support_post`): перевод
сообщений/черновика (`/translate`, `/tr-draft`, `/tr`), анализ чата (`/analyze/`),
догрузка истории (`/load-context`), смена view-cookie (`/branch-filter`).

**Fail-closed:** сессия, выпущенная до появления claim `wr` (старый cookie), не имеет
права записи до перелогина — admin'ы перелогиниваются один раз, viewer'ы остаются
read-only. Кросс-филиальную запись у пользователя со смешанными ролями дополнительно
ограничивает per-row `is_branch_forbidden` в каждом write-роуте.

> UI пока показывает viewer'у кнопки записи (Send/Edit), которые вернут 403 — граница
> безопасности серверная; скрытие контролов в UI — отдельная косметическая доработка.

**Только super_admin** (`Depends(require_super_admin)` на роутере или проверка в хендлере):

- Управление участниками — `/ui/members/**` (см. ниже).
- CRUD филиалов — `/ui/branches/**`.
- Платформенный kill-switch бота (`agent-toggle scope=platform`).
- Сырой SQLAdmin-дашборд `/admin/**` — `AdminGuardMiddleware` пропускает только
  super_admin, **всегда** (даже при `auth_enabled=false`), т.к. SQLAdmin даёт прямой
  CRUD по `membership`/`branch`/`app_setting` (иначе — самоэскалация роли).

### Управление участниками (`/ui/members/**`, super_admin only)

`_routes_members.py` + `_ui_members.py`. Таблица участников с инлайн-редакторами:

- добавить участника (telegram_id + имя + роль + филиал → `app_user` + `membership`);
- сменить роль или филиал в строке (dropdown, автосохранение);
- удалить участника.

Пункты меню «Участники» и «Филиалы» скрыты для не-super_admin
(`app_shell(..., is_super=...)`). Гард самолокаута: super_admin не может изменить роль,
сменить филиал или удалить **собственный** membership через этот UI — чтобы нельзя было
случайно лишить себя доступа.

Различие `branch_admin` vs `branch_viewer` теперь принуждается в рантайме
(`WriteGuardMiddleware` + claim `wr`, см. раздел «Роли на запись» выше).

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
- **Воркер выключен на старте** — таков был план запуска (не слать в IG параллельно со
  Stepan-1 на том же аккаунте → конфликт сессий/бан). Актуальное runtime-состояние —
  в [launch-checklist.md](launch-checklist.md).
- Stepan-1 не трогаем; полностью независимый стек.

## Фазы

Все фазы закрыты:

0. ✅ Фундамент репо (структура, core-модели, broker-only, infra, CI, docs).
1. ✅ Branch-aware ядро + изоляция + миграция знаний Индонезии.
2. ✅ Абстракция каналов (MBS чтение, IG, WhatsApp Evolution).
3. ✅ Личность лида + роутер фолоапа + чаты по продуктам.
4. ✅ RBAC + super-admin UI.
5. ✅ Деплой + тесты + проверка → готовность к старту.

## Принципы реализации

- **DI вместо синглтонов:** `branch_id`/контекст филиала передаётся явно (никаких
  глобальных `_session`/`get_settings`).
- **Единая `scoped()`-обёртка** фильтрации по филиалу — DRY, не пишем `branch_id` руками
  в каждом запросе.
- Тесты на каждую фазу: изоляция филиалов, роутинг фолоапа, сопоставление лида, RBAC.
- Документация обновляется в том же PR.

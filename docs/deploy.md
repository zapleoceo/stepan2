# Deploy — Stepan-2

Изолированный стек на том же сервере, что и Stepan-1, но **полностью отдельный**
(свой репозиторий, БД, контейнеры, порты, домен). Воркер на старте выключен.

## Цель

- Сервер: Hetzner **195.201.31.49** (тот же хост, что Stepan-1)
- Домен: **stepan2.zapleo.com** (DNS через Cloudflare → 195.201.31.49)
- API/админка: nginx TLS → `127.0.0.1:8020` (контейнер `stepan2-api`)
- Postgres и Redis: без внешнего порта — доступны только внутри docker-сети (см. `infra/docker-compose.yml`)
- Контейнеры: `stepan2-postgres`, `stepan2-redis`, `stepan2-api`, `stepan2-worker`
  (worker под docker-compose profile `worker` → не стартует по умолчанию)
- Каталог на сервере: `/var/www/stepan2`

## Принципы

- Никакого пересечения со Stepan-1: свои volume/порты/контейнеры/БД.
- Воркер не запускаем, пока не пройдём проверку и не решим cutover — чтобы не
  конфликтовать со Stepan-1 на тех же аккаунтах каналов.
- Секреты — только в `/var/www/stepan2/infra/.env` (не в VCS).
- LLM только через AIbroker (свой `BROKER_PROJECT_KEY` для Stepan-2).

## Состояние (развёрнуто)

- Стек поднят: `stepan2-postgres` (healthy), `stepan2-redis`, `stepan2-api` (Up).
  Воркер НЕ запущен (profile `worker`).
- Postgres/Redis **без внешних портов** — доступны контейнерам по сети
  (`postgres:5432` / `redis:6379`); конфликтов портов нет.
- Схема создана: `alembic upgrade head` (init 13 таблиц).
- Филиал **Indonesia** (id=1) засеян знаниями из Stepan-1: 5 docs + 13 products.
- nginx (host): `stepan2.zapleo.com` (listen 80) → `127.0.0.1:8020`. Origin отвечает
  `{"ok":true,"service":"stepan2"}`.

### Cloudflare — действие на стороне владельца

Публичный `https://stepan2.zapleo.com` не отвечает, пока в Cloudflare SSL/TLS режим не
выставлен в **Flexible** (origin слушает только 80) — либо на origin не добавлен
Cloudflare Origin Certificate и `listen 443 ssl`. Origin (nginx→api) уже работает.

## Готовность к старту (чек-лист)

- [x] Стек развёрнут изолированно (свои контейнеры/БД/volume, api на 8020).
- [x] Схема через Alembic; филиал **Indonesia** (id=1) + знания (5 docs / 13 products).
- [x] **Брокер**: проект `stepan2` в AIbroker (daily cap $10), `BROKER_URL`/`KEY` в .env,
      реальный LLM-вызов проходит (`cost_usd` учитывается).
- [x] **Instagram**: канал `itstep_jakarta` + активная `channel_session` (перенесена из
      Степан-1). `build_channel_port` собирает InstagramAdapter; instagrapi-клиент
      строится из сессии (`ds_user_id` валиден). Прокси нет (Степан-1 тоже direct).
- [x] Воркер **выключен** (compose profile `worker`).
- [x] CI + автодеплой зелёные.
- [ ] Cloudflare SSL = Flexible (origin на 80) — для публичного https.
- [ ] WhatsApp/MetaBusiness каналы — позже (учётки/токены + App Review для MBS).
- [x] **Auth включён** (`AUTH_ENABLED=true`) — см. ниже.

## Аутентификация (Telegram Login)

**На проде auth включён** — актуальное runtime-состояние (бот, пользователи, роли) см.
в [launch-checklist.md](launch-checklist.md). Дефолт кода — выключен
(`AUTH_ENABLED=false`), чтобы выкатка на свежий стенд не заблокировала вход до
настройки бота/домена. Пока выключен — `/ui` и `/admin` открыты; на это время поставь
nginx basic-auth или IP-allowlist перед `:8020`.

Нюанс env-префикса: приложение читает переменные с префиксом `STEPAN2_`, а в `.env`
они лежат без него — маппинг делает compose (`infra/docker-compose.yml`:
`STEPAN2_AUTH_ENABLED: ${AUTH_ENABLED:-false}`, аналогично `SESSION_SECRET`,
`TG_BOT_TOKEN`, `TG_LOGIN_BOT_USERNAME` и пр.).

**Чтобы включить:**

1. Создать (или выделить) Telegram-бота для логина и привязать домен:
   BotFather → `/setdomain` → `stepan2.zapleo.com`. Один бот = один домен, поэтому
   нельзя переиспользовать login-бота Степан-1.
2. В `/var/www/stepan2/infra/.env` задать:
   - `TG_BOT_TOKEN=` — токен этого бота (нужен и для проверки логина, и для алертов);
   - `TG_LOGIN_BOT_USERNAME=` — его username (без `@`);
   - `SESSION_SECRET=` — длинная случайная строка (пусто → возьмётся `SECRET_KEY`);
   - `BOOTSTRAP_SUPER_ADMIN=169510539` — Дима; self-heal входа владельца;
   - `AUTH_ENABLED=true`.
3. Засеять пользователей (идемпотентно):
   `docker exec -e PYTHONPATH=/app stepan2-api python -m app.modules.auth.bootstrap`.
4. Передеплоить api (git push → Actions). Вход: `https://stepan2.zapleo.com/login`.

Доступ по ролям: `super_admin` (branch_id=NULL) видит все филиалы; `branch_admin`/
`branch_viewer` — только свой. Branch-фильтр в UI уже не может вывести филиал за
пределы прав пользователя (cookie сужается до allowed-набора).

## Тюнинг (env-настройки)

Операционные параметры (анти-бан-тайминг, воркер-капы, LLM-стоимость, таймауты,
ретенция, версии внешних API) вынесены в `app/config.py` как `STEPAN2_*` env-переменные.
Каждое поле имеет `description=` с объяснением — **это единственный источник истины**,
markdown-копию тут намеренно не держим (чтобы не расходилась).

Ключевое: `worker_job_timeout_s` (120с) — под него подогнаны `reply_batch_cap`,
`send_batch_cap`, `deletion_thread_cap` и брокерский `llm_read_timeout_slow_s` (90с);
двигать их нужно согласованно, иначе ARQ убивает тик по таймауту и ретрай шлёт дубли.
Анти-бан-тайминг (`bubble_gap_s`, `seen_delay_min/max_s`, `soft_block_retry_min`,
`ingest_jitter_s`) концептуально пер-филиальный, но пока глобальный env.

Переопределяются через `.env` на сервере; изменение требует рестарта воркера/API.

## Cutover (выключить Степан-1 → включить Степан-2)

IG-аккаунт один (itstep_jakarta) — нельзя, чтобы оба воркера слали одновременно
(challenge/бан). Поэтому строго по очереди:

```bash
# 1) Остановить воркер Степан-1
cd /var/www/stepan/infra   && docker compose stop ig-worker
# 2) Запустить воркер Степан-2
cd /var/www/stepan2/infra  && docker compose --profile worker up -d worker
# Откат (вернуться на Степан-1):
cd /var/www/stepan2/infra  && docker compose --profile worker stop worker
cd /var/www/stepan/infra   && docker compose start ig-worker
```

API/админка Степан-2 могут работать всё время (читают БД); конфликт только на уровне
воркера и IG-сессии.

## CI/CD

GitHub Actions: CI (ruff + pytest) на каждый push. Деплой (`deploy.yml`, rsync + docker
compose + alembic) на `main`, secrets `HETZNER_HOST` / `HETZNER_PORT` / `HETZNER_SSH_KEY`
настроены в репозитории stepan2.

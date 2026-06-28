# Deploy — Stepan-2

Изолированный стек на том же сервере, что и Stepan-1, но **полностью отдельный**
(свой репозиторий, БД, контейнеры, порты, домен). Воркер на старте выключен.

## Цель

- Сервер: Hetzner **195.201.31.49** (тот же хост, что Stepan-1)
- Домен: **stepan2.zapleo.com** (DNS через Cloudflare → 195.201.31.49)
- API/админка: nginx TLS → `127.0.0.1:8020` (контейнер `stepan2-api`)
- Postgres: `127.0.0.1:5435` · Redis: `127.0.0.1:6380` (только localhost)
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

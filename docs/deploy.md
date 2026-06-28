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

## CI/CD

GitHub Actions: CI (ruff + pytest) на каждый push. Деплой (`deploy.yml`, rsync + docker
compose + alembic) на `main` требует secrets `HETZNER_HOST` / `HETZNER_PORT` /
`HETZNER_SSH_KEY` в репозитории stepan2.

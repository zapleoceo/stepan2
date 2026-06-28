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

## CI/CD

GitHub Actions (`.github/workflows/`): CI (ruff + pytest) на каждый push; деплой
(rsync + docker compose) на `main` — настраивается в Фазе 5.

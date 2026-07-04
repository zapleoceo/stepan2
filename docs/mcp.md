# MCP-коннектор (управление воронкой извне)

Внешняя система (или Claude Desktop) двигает лида по воронке **по номеру телефона**
через MCP. Транспорт — локальный stdio-сервер, который ходит в защищённый HTTP-API
Stepan.

```
Claude Desktop ──stdio──▶ mcp_server/stepan_mcp.py ──HTTPS+Bearer──▶ stepan2.zapleo.com/mcp/*
```

Два транспорта:

- **Local stdio** (`mcp_server/stepan_mcp.py`) — для клиента с локальным Python
  (Claude Desktop, Claude Code). Инструкция — в [`mcp_server/README.md`](../mcp_server/README.md).
- **Remote Streamable HTTP** (`app/api/mcp_remote.py`, смонтирован на `/connector/mcp`) —
  для клиентов, которым можно дать только URL (claude.ai web «Custom connector»).
  Токен передаётся заголовком `Authorization: Bearer` **или** `?key=<token>` в URL
  (capability-URL для веб-клиентов без заголовков). DNS-rebinding-защита выключена
  (за Cloudflare/nginx Host непредсказуем; каждый запрос и так за токен-гейтом).

`MCP_SECRET` может содержать **несколько токенов через запятую** — каждому вызывающему
(владелец, партнёр, интеграция) свой, независимо отзываемый.

## Инструменты

| Tool | Действие |
|------|----------|
| `find_lead(phone)` | Найти лида по телефону (E.164) → id, имя, IG, стадия, бот вкл/выкл |
| `move_lead(phone, stage, note?)` | Явно поставить стадию (`new`…`manager`) |
| `close_deal(phone, note?)` | Сделка закрыта → `handed_off`, бот выключается |
| `call_failed(phone, note?)` | Не дозвонились → пометка в журнал, бот включается, Степан сам пишет лиду в чат, чтобы продолжить перепиской |

`call_failed`: лид в «тихой» стадии (`ready`/`handed_off`/`dormant`/`manager`)
возвращается в `qualifying`, чтобы бот снова его вёл.

## Сервер

- API смонтирован в основном приложении: `POST/GET /mcp/*` (см. `app/api/_routes_mcp.py`).
- Доменные операции: `app/modules/leads/ops.py` (без прямого доступа к БД в роутах).
- Авторизация: заголовок `Authorization: Bearer <STEPAN2_MCP_SECRET>`.
  Секрет живёт в `infra/.env` (`MCP_SECRET=…`), пробрасывается в контейнер через
  `STEPAN2_MCP_SECRET`. Пусто → API отвечает `403` (выключен).
- Сессионная авторизация (`/ui`, `/admin`) для `/mcp/*` **не применяется** — гейт
  только по bearer-токену.
- Схема БД не менялась (используются существующие `lead` / `stage_event` / `outbox`).

## Проверка

```bash
curl -s "https://stepan2.zapleo.com/mcp/find_lead?phone=%2B6289629979734" \
     -H "Authorization: Bearer $MCP_SECRET"
```

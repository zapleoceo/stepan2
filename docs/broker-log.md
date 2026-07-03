# Лог вызовов брокера

Каждый вызов к AIbroker пишется одной строкой в `broker_log` — единственная точка
записи — из [`BrokerLLM`](../app/adapters/llm/broker.py) (`_log_call`). У брокера есть
цена/латентность/провайдер, но нет `thread_id` и типа вызова — их знает только Степан.

## Что логируется

Все пути через `BrokerLLM`: ответы (`reply`), follow-up (`followup`), перевод
(`translate`), эмбеддинги (`embed`), правки базы Coach (`coach`), черновики менеджеру
(`suggest`). И успехи, и ошибки (`ok=false` + `error` — текст ответа брокера, полезно
для разбора `BadRequestError`/`RateLimitError`).

## Поля

`request_id` (сверка с брокером) · `branch_id` · `thread_id` · `kind` (workflow) ·
`capability` · `provider` · `model` · `tokens_in/out` · `cost_usd` · `latency_ms` ·
`ok` · `error` · `created_at`. См. модель `BrokerLog` в
[`models.py`](../app/adapters/db/models.py), миграция `a1b2c3d4e5f6`.

## Просмотр

Страница `/settings/log` (вкладка «Лог брокера»). Пагинация по 50, новые сверху.
Фильтруется по `branch_id`: не-владелец видит только свои филиалы, владелец — все.
Запрос — [`fetch_broker_log`](../app/api/_query.py), рендер —
`broker_log_panel_html` в [`_ui_panels.py`](../app/api/_ui_panels.py).

## Ретенция

Cron `prune_broker_log` в [воркере](../app/worker/main.py) раз в сутки (03:30) чистит
строки старше 15 дней — таблица остаётся ограниченной.

## Fail-safe

Ошибка записи лога **никогда** не должна ронять ответ лиду: `_log_call` глотает любое
исключение и только пишет `warning`. Лог — не критичный путь.

## Двойной биллинг одного треда (race, устранено)

Инцидент: `reply_pending` — cron раз в минуту, до 10 параллельных ARQ-джобов. Если тик
ещё выполняется, когда наступает следующий, оба читают
[`threads_awaiting_reply`](../app/worker/wiring.py) до того, как первый закоммитит
outbox-строку — оба проходят `NOT EXISTS pending` guard и оба вызывают LLM для одного
треда (реальный кейс: тред 1585, mistral + deepseek с разницей 47с, $0.0027 потрачено
впустую на дубль без исходящего сообщения).

Фикс — `pg_try_advisory_xact_lock(thread_id)` в
[`wiring.try_lock_thread`](../app/worker/wiring.py), взятый в `_reply_thread`
([`worker/main.py`](../app/worker/main.py)) **до** `ReplyService.decide()` (до LLM-вызова).
Лок держится в рамках транзакции и снимается автоматически на commit/rollback — снимать
руками не нужно. No-op вне Postgres (sqlite в тестах не конкурентно).

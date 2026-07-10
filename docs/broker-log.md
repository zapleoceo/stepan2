# Лог вызовов брокера

Каждый вызов к AIbroker пишется одной строкой в `broker_log` — единственная точка
записи — из [`BrokerLLM`](../app/adapters/llm/broker.py) (`_log_call`). У брокера есть
цена/латентность/провайдер, но нет `thread_id` и типа вызова — их знает только Степан.

## Что логируется

Все пути через `BrokerLLM`: ответы (`reply`), follow-up (`followup`), перевод
(`translate`), эмбеддинги (`embed`), правки базы Coach (`coach`), черновики менеджеру
(`suggest`). И успехи, и ошибки (`ok=false` + `error` — текст ответа брокера, полезно
для разбора `BadRequestError`/`RateLimitError`).

## Все chat-вызовы — async job queue (submit + poll), не блокирующий вызов

С 2026-07-09 **весь** chat идёт через очередь заданий брокера, а не синхронный
`POST /v1/chat`: `BrokerLLM.chat()`/`chat_deep()` делают `POST /v1/jobs?capability=X`
(тело — то же, что у синхронного /v1/chat, включая `response_format`) → получают
`job_id` сразу → опрашивают `GET /v1/jobs/{job_id}` с интервалом `poll_after_s` из
ответа брокера, пока статус не станет `done`/`error`, либо не истечёт общий бюджет
(`_poll_budget_s`: `llm_read_timeout_deep_s` для deep, `_slow_s` для smart, `_s` для
остального — теперь это **бюджет опроса**, а не таймаут одного HTTP-запроса; каждый
submit/poll использует короткий `_JOB_HTTP_TIMEOUT`, read=30с). Так медленный
провайдер больше не держит синхронное соединение мимо таймаутов Cloudflare/nginx
(класс ошибок 504). Опрос терпит до `_POLL_MAX_ERRORS`=5 транзиентных ошибок подряд
(502/timeout), чтобы не выбросить уже запущенное задание. Первый poll — сразу после
submit (быстрое задание может быть уже `done`, без лишнего ожидания).

Единая реализация — приватный `_submit_and_poll` (DRY): `chat()` и `chat_deep()` —
тонкие обёртки над ним. `chat_deep()` дополнительно фоллбэчит на `chat:smart`, если
`llm:deep` ещё не выдан на project key (submit вернул 403). `embed()`/`transcribe()`
остаются синхронными (быстрые, у брокера нет job-эндпоинта для них).

`broker_log`-контракт не изменился: `_log_call` пишет одну строку на вызов с тем же
`capability`/`request_id` (= `usage_log.id` брокера), так что аудит-страница и поиск
по `request_id` работают как раньше.

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
строки старше 30 дней — таблица остаётся ограниченной.

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

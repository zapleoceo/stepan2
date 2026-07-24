# Stepan-2 — документация

Мультифилиальная платформа AI-продаж. Каждый филиал изолирован (`branch_id`): своя
база знаний, продукты, персона, язык, пользователи, каналы. Строится независимо от
Stepan-1.

## Карта

| Документ | О чём |
|---|---|
| [multitenant-design.md](multitenant-design.md) | Архитектура мультиарендности, каналы, личность лида, миграция, фазы |
| [lead-identity-and-deletion.md](lead-identity-and-deletion.md) | Объединение лидов по телефону, каскад удаления канала, инвариант лида-сироты |
| [broker-log.md](broker-log.md) | Лог вызовов брокера: что логируется, поля, страница `/settings/log`, ретенция |
| [knowledge-base.md](knowledge-base.md) | База знаний (факты-только): каноническая структура, факты целиком в промпт каждый ход, язык, UI-дерево, история правок |
| [free-mode.md](free-mode.md) | **Reply pipeline** (единственный): цель вместо скрипта, кэшируемый префикс, chat:sales (Sonnet-first) с фолбэком, money-gate, фолоу-апы/реактивация на том же билдере |
| [dialogue-qa-checklist.md](dialogue-qa-checklist.md) | **Регрессия диалогов**: чеклист найденных-и-починенных ошибок + как прогонять sim (только на ClodeCouch, branch 8). Обновляется при каждой новой ошибке |
| [ad-attribution-and-reports.md](ad-attribution-and-reports.md) | Атрибуция рекламы, авто-привязка продукта (`ad_product_map`), провенанс `product_source` |
| [launch-checklist.md](launch-checklist.md) | Готовность к продакшену: что задать (env, auth, бот), статус уведомлений/KB/профилей |
| [deploy.md](deploy.md) | Деплой: изолированный стек на Hetzner, nginx/Cloudflare, auth (Telegram Login), cutover со Stepan-1, CI/CD |
| [worker.md](worker.md) | ARQ-воркер: cron-задачи, капы/тихие часы outbox, заморозка сессии при challenge, advisory-lock |
| [chat-panel-perf.md](chat-panel-perf.md) | Открытие чата: запросы панели, фоновая (lazy) загрузка перевода needs, оптимизация медиа-превью сообщений |

## Конвенции

- Одна тема — один файл; ссылки на код (`path:line`) вместо дублирования.
- Документацию обновляем **в том же PR**, что и код.
- Никаких секретов, токенов, реальных данных клиентов в `docs/`.
- Изоляция филиалов — security-инвариант: каждый запрос к данным фильтруется по `branch_id`.
- **Симуляции диалогов — только на ClodeCouch (`branch_id=8`), НЕ на боевой Индонезии
  (`branch_id=1`).** Найдена новая ошибка диалога → после фикса добавь строку в
  [dialogue-qa-checklist.md](dialogue-qa-checklist.md).

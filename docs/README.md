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
| [prompt-library.md](prompt-library.md) | **Три слоя промта** (CRAFT в коде / METHOD и BUSINESS — данные филиала), версионная библиотека персон·методов·каталогов, клон в филиал, переключатель `prompt_pipeline` (legacy\|composer), `in_prompt` вместо жёсткого списка слагов |
| [website-branch.md](website-branch.md) | **Чат на сайте — свой изолированный филиал (S6)**: коннектор `website` (без отправки, без опроса, `proactive_outreach=False`), промт из библиотеки вместо константы `_SYSTEM`, серверная переписка по подписанному токену, рейт-лимит по неподделываемому хопу |
| [free-mode.md](free-mode.md) | **Reply pipeline** (единственный): цель вместо скрипта, кэшируемый префикс, chat:sales (Sonnet-first) с фолбэком, money-gate, фолоу-апы/реактивация на том же билдере |
| [dialogue-qa-checklist.md](dialogue-qa-checklist.md) | **Регрессия диалогов**: чеклист найденных-и-починенных ошибок + как прогонять sim (только на ClodeCouch, branch 8). Обновляется при каждой новой ошибке |
| [ad-attribution-and-reports.md](ad-attribution-and-reports.md) | Атрибуция рекламы, авто-привязка продукта (`ad_product_map`), провенанс `product_source` |
| [launch-checklist.md](launch-checklist.md) | Готовность к продакшену: что задать (env, auth, бот), статус уведомлений/KB/профилей |
| [deploy.md](deploy.md) | Деплой: изолированный стек на Hetzner, nginx/Cloudflare, auth (Telegram Login), cutover со Stepan-1, CI/CD |
| [worker.md](worker.md) | ARQ-воркер: cron-задачи, капы/тихие часы outbox, заморозка сессии при challenge, advisory-lock |
| [proactive-comments.md](proactive-comments.md) | **Комментарии под чужими постами**: единственная миссия, где бот пишет первым. Судья chat:fast, автор chat:smart, три потолка лимитов, что сознательно не сделано |
| [chat-panel-perf.md](chat-panel-perf.md) | Открытие панелей: запросы чата, lazy-перевод needs, медиа-превью, перевод комментариев в почасовом воркере, протухшие аватарки IG |
| [tech-debt.md](tech-debt.md) | **Отложенные работы**: что решили не делать сейчас и почему. Сейчас в списке — официальный вебхук Meta вместо опроса приватным API |

## Конвенции

- Одна тема — один файл; ссылки на код (`path:line`) вместо дублирования.
- Документацию обновляем **в том же PR**, что и код.
- Никаких секретов, токенов, реальных данных клиентов в `docs/`.
- Изоляция филиалов — security-инвариант: каждый запрос к данным фильтруется по `branch_id`.
- **Симуляции диалогов — только на ClodeCouch (`branch_id=8`), НЕ на боевой Индонезии
  (`branch_id=1`).** Найдена новая ошибка диалога → после фикса добавь строку в
  [dialogue-qa-checklist.md](dialogue-qa-checklist.md).

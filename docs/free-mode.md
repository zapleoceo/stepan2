# Reply pipeline — свободный режим продаж (единственный)

Как Степан отвечает лиду: короткая цель + ПОЛНАЯ фактовая поверхность, и сильная модель
сама решает, как продавать. Жёстким остаётся только money-gate.

История: скриптовый путь (13 ходов, discovery-лестница, 9 turn-notes, pitch/answer-гейты,
LLM-критик, skeleton-opener) удалён 2026-07-25 после sim A/B на 10 персонах (branch 8):
свободный режим дал 6/10 явных согласий против 3/10 и 0/10 принудительных эскалаций против
8/10. Флага `reply_mode` больше нет — это единственный путь; откат = git revert + деплой.

## Цель (зашита в контракт `free_mode.py`)

1. Довести лида до **явного согласия** записаться на курс.
2. Взять телефон/WhatsApp — менеджер звонит в рабочие часы 09:00–18:00 WIB (не дозвонился →
   WhatsApp); вне этих часов бот обещает контакт «с 9 утра следующего рабочего дня».
3. Лид готов платить сейчас → бот сам даёт варианты оплаты из KB, не паркует горячего лида.

## Структура промпта — кэшируемый префикс

`build_messages_free` (`app/modules/conversation/free_mode.py`):

- `messages[0]` (system) — **байт-стабильный префикс**: полная KB
  (`KnowledgeService.full_knowledge_context`: persona_core + все facts-доки целиком + весь
  objection_playbook + ВСЕ карточки продуктов, сортировка по slug) + контракт + JSON-схема.
  Одинаков между ходами и между лидами одного филиала/языка — якорь prompt-кэша брокера.
  Стабилен в пределах локального дня (единственный датозависимый вход — `annotate_dates`).
- `messages[1]` (system) — всё переменное: now-block, manager rules/note, entry-hint, имя,
  LEAD DOSSIER, first-turn note.
- Дальше диалог.

**Инвариант:** никаких условных вставок в `messages[0]` — любая ломает кэш и множит счёт
Sonnet. Закреплено тестом `test_prompt_prefix_is_byte_stable_across_leads_and_turns`.
Потолок префикса — `free_context_char_budget` (90k, `app/config.py`).

## Роутинг

`routing.pick_capability` (структурный, по досье) выбирает tier; `SMART → chat:sales`
(Sonnet-first цепочка брокера), рутинные ходы → `chat:fast`. Фолбэк (`reply._generate`):
транспортная ошибка ИЛИ непарсибельное тело на `chat:sales` → один повтор на `chat:smart` —
деградация к дешёвой цепочке, не к молчанию. Anthropic-квирк: решение может прийти в
tool-конверте `{"parameters": {...}}` — парсер разворачивает
(`decision._unwrap_tool_envelope`).

## Схема ответа

`reply/move/stage/product_slug/ready/phone/needs_human/reply_language/dossier`. `move` —
свободный snake_case-ярлык модели (телеметрия, не гейт). Досье накапливается
(`merge_dossier` + discovery-backstop) — на нём роутинг, фолоу-апы, CRM, аналитика.

## Что остаётся жёстким

- **Money-gate** (`money_gate.money_issues`, fail-closed): цены/ссылки/доход/выдуманные
  услуги только из KB; один rewrite на `chat:sales`, затем hold-line + эскалация.
- Чисто шаблонные openers (AD_SILENT/STORY/JUNK, `opener.py`) — анти-бан, ноль LLM.
  Typed-входы идут в полный пайплайн.
- Вся доставка: бабблы, капы, тихие часы, стадии, hand-off, CRM-push (`delivery.py`).

## Строка брокера на баббле (`llm_info`)

Каждый исходящий баббл несёт чип `🤖 время | #request_id | цена | токены | модель`.
Источник — `_fmt_llm_meta(meta)` из мета брокера; `ReplyService` кладёт мету последней
генерации (включая money-rewrite: платит и отгружается именно она) в `_last_llm_meta`,
`enqueue_reply` штампует её во ВСЕ бабблы одного ответа, `outbox._outgoing` переносит поле
в `message` при отправке. Шаблонный ход без брокера пишет `templated | free` — пустой чип
означал бы потерю меты, а не её отсутствие (регресс 2026-07-22…07-25: v3-движок не
сохранял мету вообще, 100% agent-бабблов на проде были без чипа). Ручные сообщения
менеджера чипа не имеют — они подписаны именем менеджера.

## Фолоу-апы / реактивация / call-failed

Все три нуджа собираются тем же `build_messages_free` + `engine.free_kb_context` (единый
кэшируемый префикс) с собственным framing-сообщением последним ходом (`followup.py`,
`reactivation.py`, `leads/ops.py`). Нудж с непрошеной ценой (`uninvited_price`) получает
один rewrite, затем дропается; пустой ответ = «нечего сказать» → шаг сгорает.

## База знаний

Скелет KB (`knowledge/canonical_docs.py`) = ровно 4 документа, которые реально уходят в
промпт: `persona_core`, `facts_policy`, `facts_market`, `objection_playbook` + карточки
продуктов. Метод продаж живёт в модели, KB держит только факты.

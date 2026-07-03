# База знаний, RAG и история правок

Как устроена база знаний филиала, как она попадает в промпт через RAG, как
редактируется в UI и как хранится история.

## Модель данных

| Таблица | Что | Ключевые поля |
|---|---|---|
| `knowledge_doc` | Документы (персона, playbook'и, справочник) | `slug`, `title`, `category`, `sort_order`, `content`, `updated_at`, `updated_by` |
| `product` | Карточки продуктов | `slug`, `title`, `content`, `is_active`, `sort_order`, `updated_at`, `updated_by` |
| `knowledge_chunk` | Куски для RAG-поиска | `source_type`, `source_slug`, `title`, `seq`, `text`, `embedding` (JSON-вектор) |
| `knowledge_revision` | История правок doc/product | `entity_type`, `slug`, `old_content`, `new_content`, `actor`, `created_at` |

Все таблицы несут `branch_id` — изоляция филиалов в репозитории. Схема: миграция
`b2c3d4e5f6a7`.

## Каноническая структура

[`canonical_docs.py`](../app/modules/knowledge/canonical_docs.py) задаёт дефолтный
скелет, который получает **каждый** филиал: набор slug'ов S1 (persona_core, faq,
market_facts, stories, playbook_×7) по категориям (`persona` / `playbook` /
`reference`), с локализованными (ru/en/id) заголовками секций и **плейсхолдерами-
подсказками** «что здесь должно быть». `ensure_canonical_docs`
([canonical.py](../app/modules/knowledge/canonical.py)) создаёт недостающие доки и
проставляет `category`/`sort_order`/`title` существующим, **не трогая `content`** —
вызывается при создании филиала и в `seed_branch`. Доки филиала вне канона (например
`openhouse`) попадают в группу «Прочее».

Контент дока — markdown с `## `-секциями; подсказки рендерятся на языке интерфейса,
а сам контент может быть на **любом** языке.

## RAG (единственный путь знаний в промпт)

Прямого текстового фолбэка нет: `KnowledgeService.knowledge_context`
([service.py](../app/modules/knowledge/service.py)) собирает **персону** (persona_core,
напрямую) + **каталог** продуктов + **карточку** сфокусированного продукта +
**retrieved-чанки** под текущий диалог. Объёмные playbook'и/faq/stories доходят до
модели только через поиск.

- Чанкинг: [`chunking.py`](../app/modules/knowledge/chunking.py) — по `## `-секциям,
  жадная упаковка ~1400 символов, заголовок остаётся в чанке.
- Индекс: [`rag.py`](../app/modules/knowledge/rag.py) `RagService` — chunk → embed
  (брокер, Voyage) → `knowledge_chunk`. Персона в индекс не идёт (всегда в промпте).
  Эмбеддинги — JSON-массивы, cosine считается в Python (работает и на SQLite-тестах,
  и на Postgres).
- Retrieval: запрос = последние сообщения диалога (`_retrieval_query` в
  [reply.py](../app/modules/conversation/reply.py)); top-12 по cosine.

### Вотчер переиндексации

[`reindex.py`](../app/modules/knowledge/reindex.py): филиал «протух», если любой
doc/product изменён позже его водяного знака (`app_setting` ключ `rag_indexed_at`).
Крон `reindex_knowledge` в [воркере](../app/worker/main.py) раз в 5 минут
переиндексирует протухшие филиалы; каждый — в своей транзакции. Кнопка ⟳ в шапке
дерева знаний форсирует переиндексацию сейчас.

## Язык

KB может быть на любом языке; отвечает Степан на языке филиала (`Branch.lang`) или на
`Lead.preferred_language`, если лид попросил другой. Модель возвращает
`reply_language` когда лид переключил язык — он сохраняется на лиде, чтобы follow-up'ы
тоже шли на нём. Формулировка промпта: KB — источник фактов, язык ответа отдельно
([prompt.py](../app/modules/conversation/prompt.py)).

## UI (`/ui/knowledge`)

Две вкладки сверху — **Персона** (дерево доков по категориям) и **Продукты**.
Навигация только по сайдбару; клик открывает контекст-панель справа. Редактор —
посекционный (textarea на каждую `## `-секцию; пустой канонический док показывает
скелет с подсказками). Рендер: [`_ui_kb.py`](../app/api/_ui_kb.py), роуты:
[`_routes_knowledge.py`](../app/api/_routes_knowledge.py).

## История правок

Каждое изменение `content` дока/продукта журналируется в `knowledge_revision`
([history.py](../app/modules/knowledge/history.py)) — кто (`actor`), что (old→new),
когда. Пишется из app-слоя (портируемо, actor известен), no-op и правки только
заголовка пропускаются. По кнопке 🕘 — просмотр с unified-diff и **восстановлением**
любой версии (восстановление тоже журналируется).

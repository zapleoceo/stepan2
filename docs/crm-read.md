# CRM read-gate (Степан не трогает лида, которого уже ведёт менеджер)

Два направления связи с CRM:

- **Push** (было): `ManagerAlert` → вебхук CRM (`crm_enabled` + `crm_webhook_url`).
  См. `CrmSyncService`.
- **Read / gate** (это): перед каждым авто-сообщением Степан спрашивает CRM состояние
  лида по телефону и **молчит**, если по лиду уже есть движение.

## Что просить у Виктора (контракт)

Один эндпоинт «состояние лида по телефону»:

```
GET  <crm_state_url>?phone=+62812...        Authorization: Bearer <crm_read_secret>
→ 200 application/json
{
  "exists":          true,                    // есть ли клиент в CRM (404 = нет — Степан пишет как обычно)
  "status":          "in_progress",           // текущий статус лида/клиента
  "owner":           "manager",               // кто ведёт: manager | bot | none
  "manager_called":  true,                    // был ли звонок менеджера
  "next_contact_at": "2026-07-05T09:00:00Z",  // назначен ли следующий контакт (null=нет)
  "open_task":       true,                     // есть ли открытая задача
  "deal_won":        false,                    // заключена ли сделка
  "contract_signed": false,                    // оформлен ли договор
  "paid":            false,                     // есть ли оплата
  "verdict":         "hold",                   // опц.: proceed|hold — если CRM решает сама
  "reason":          "manager owns; call done"
}
```

`verdict` необязателен: если CRM его не отдаёт, Степан выводит сам —
**hold**, если `owner=manager` ИЛИ `deal_won` ИЛИ `contract_signed` ИЛИ `paid` ИЛИ
`open_task` ИЛИ `manager_called` ИЛИ задан `next_contact_at`; иначе **proceed**
(`compute_verdict` в `app/modules/crm/gate.py`).

## Поведение Степана

- **Точечно перед отправкой** (`OutboxSender._crm_gate`): при `hold` реплика не
  отправляется (`status=skipped`, не переотправляется), лид → `manager`, бот off,
  запись в журнал воронки (`actor=crm`).
- **Периодически** (крон `sync_crm`, `CrmPullService`): подтягивает состояния активных
  лидов (самые несвежие первыми), греет кэш и превентивно тормозит `hold`-лидов.
- **Fail-open**: гейт выключен, нет телефона, лида нет в CRM или CRM недоступна —
  сообщение **уходит**. Сбой CRM никогда не глушит живого бота.
- **Ручные сообщения** менеджера гейт не трогают.

Кэш — таблица `crm_lead_state` (одна строка на лида). Свежее `crm_state_ttl_s` (деф.
300 c) берётся из кэша; старше — точечная проверка перечитывает вживую.

## Включение (когда Виктор отдаст эндпоинт)

Настройки филиала (`app_setting`): `crm_read_enabled=true`, `crm_state_url=https://…`,
`crm_read_secret=…`. URL обязан быть `https` и не приватным (SSRF-guard). По умолчанию
флаг **OFF** — код в проде ничего не меняет, пока не включат.

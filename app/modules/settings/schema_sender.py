"""Раздел настроек «Переписка через CRM» (sender).

СОЗНАТЕЛЬНО отдельно от раздела CRM, хотя оба ведут в одну компанию и могут указывать даже
на один и тот же сервер. Это две разные связи с разной судьбой:

  CRM (crm_mcp_url) — мы СПРАШИВАЕМ о лиде и ОТДАЁМ туда события. Это учёт: карточка,
  стадия, сделка. Сломается — Степан продолжает говорить с лидом, просто вслепую.

  sender (эти ключи) — мы ЧИТАЕМ, что лид написал, и ОТВЕЧАЕМ ему. Это сам разговор.
  Сломается — лид молчит в пустоту, и никакой учёт этого не заменит.

Один адрес на двоих означал бы, что отключение учёта затыкает разговор, а смена токена у
одного ломает другое. Поэтому ключи свои, и «оба одинаковые» — это допустимое совпадение
значений, а не общая настройка.

Пофилиально, как и CRM: у второго арендатора будет свой sender, и заводить его правкой
исходников недопустимо.
"""
from __future__ import annotations

from .fields import SettingSection
from .fields import i18n as _l
from .fields import setting as _f

SENDER_SECTION = SettingSection(
    "fa-brands fa-whatsapp",
    _l("Переписка через CRM", "Messaging via CRM", "Perpesanan lewat CRM"),
    [
        _f("sender_enabled", "bool", "false",
           _l("Отвечать лидам через CRM", "Reply to leads via CRM",
              "Balas lead lewat CRM"),
           help=_l("Входящие приносит колбек, ответ уходит инструментом sender",
                   "The callback brings inbound; replies go out through the sender tool",
                   "Callback membawa pesan masuk; balasan lewat tool sender"),
           width="150px"),
        _f("sender_mcp_url", "secret", "",
           _l("MCP-сервер sender (URL с токеном)", "Sender MCP server (URL with token)",
              "Server MCP sender (URL dengan token)"),
           ph=_l("https://…/mcp/sender?token=…", "https://…/mcp/sender?token=…",
                 "https://…/mcp/sender?token=…"),
           help=_l("Отдельно от MCP-сервера CRM, даже если адрес тот же. Пусто — филиал "
                   "не отвечает через CRM. Пусто при сохранении = не менять",
                   "Separate from the CRM MCP server, even when the address matches. Empty "
                   "means this branch does not reply via CRM. Blank on save = keep current",
                   "Terpisah dari server MCP CRM meski alamatnya sama. Kosong = cabang ini "
                   "tidak membalas lewat CRM"),
           width="340px"),
        _f("sender_project", "text", "",
           _l("Проект в sender (alias)", "Sender project (alias)", "Proyek sender (alias)"),
           ph=_l("crm", "crm", "crm"),
           help=_l("Буквенный alias — его ждёт инструмент отправки",
                   "The letter alias the send tool expects",
                   "Alias huruf yang diminta tool pengiriman"),
           width="130px"),
        _f("sender_project_id", "text", "",
           _l("Проект в sender (число)", "Sender project id", "ID proyek sender"),
           ph=_l("6", "6", "6"),
           help=_l("То же самое числом — в таком виде приходит в колбеке. Выводить одно из "
                   "другого нечем: соответствия у нас нет",
                   "The same thing as a number — that is how the callback sends it. Neither "
                   "can be derived from the other; we hold no mapping",
                   "Hal yang sama dalam angka — begitulah callback mengirimnya"),
           width="110px"),
        _f("sender_branch_id", "text", "",
           _l("Филиал в sender (число)", "Sender branch id", "ID cabang sender"),
           ph=_l("435", "435", "435"),
           help=_l("Ошибка здесь уводит ответ в чужой филиал",
                   "A mistake here sends the reply to another branch",
                   "Kesalahan di sini mengirim balasan ke cabang lain"),
           width="110px"),
    ],
)

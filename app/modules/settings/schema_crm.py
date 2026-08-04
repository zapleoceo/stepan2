"""The CRM settings section.

Four of these keys had no editor anywhere in the product, so a branch could not override
the platform-tier CRM link it was silently inheriting even if it knew about it. The
inheritance itself is stopped in tenant_keys/repository; these fields are what let a branch
name its own CRM instead. The city alias no longer defaults to the first client's city.
"""
from __future__ import annotations

from .fields import SettingSection
from .fields import i18n as _l
from .fields import setting as _f

CRM_SECTION = SettingSection("fa-solid fa-database", _l("CRM", "CRM", "CRM"), [
    _f("crm_enabled", "bool", "false",
       _l("Слать лиды в CRM", "Send leads to CRM", "Kirim lead ke CRM"), width="130px"),
    _f("crm_webhook_url", "secret", "",
       _l("CRM webhook URL", "CRM webhook URL", "CRM webhook URL"),
       ph=_l("https://…", "https://…", "https://…"),
       help=_l("POST manager_alert на этот URL", "POST manager_alert here",
               "POST manager_alert ke URL"), width="340px"),
    _f("crm_mcp_url", "secret", "",
       _l("MCP-сервер CRM (URL с токеном)", "CRM MCP server (URL with token)",
          "Server MCP CRM (URL dengan token)"),
       ph=_l("https://…/mcp/crm?token=…", "https://…/mcp/crm?token=…",
             "https://…/mcp/crm?token=…"),
       help=_l("Свой на каждый филиал. Пусто — филиал не ходит в CRM. Пусто при "
               "сохранении = не менять",
               "One per branch. Empty means this branch talks to no CRM. Blank on save "
               "= keep current",
               "Satu per cabang. Kosong = cabang ini tidak terhubung ke CRM. Kosong saat "
               "simpan = tetap"),
       width="340px"),
    _f("crm_mcp_city_alias", "text", "",
       _l("Город (cityAlias) в CRM", "CRM city alias", "Alias kota di CRM"),
       ph=_l("jakarta", "jakarta", "jakarta"),
       help=_l("Какую площадку CRM спрашивать о лидах этого филиала",
               "Which CRM tenancy to ask about this branch's leads",
               "Tenancy CRM mana yang ditanya soal lead cabang ini"),
       width="170px"),
    _f("crm_rescue_enabled", "bool", "false",
       _l("Подхватывать недозвоны", "Pick up missed calls", "Tindak lanjuti panggilan gagal"),
       help=_l("По журналу звонков CRM: кому не дозвонились — тому Степан пишет в чат",
               "From the CRM call log: leads the phone could not reach get a DM instead",
               "Dari log panggilan CRM: lead yang tak terjangkau dikirimi DM"),
       width="150px"),
    _f("crm_writeback_enabled", "bool", "false",
       _l("Заводить тёплых лидов в CRM", "Push warm leads into CRM",
          "Kirim lead hangat ke CRM"),
       help=_l("Степан создаёт карточку в воронке CRM, когда лид разогрет",
               "Stepan creates the CRM funnel card once a lead is warm",
               "Stepan membuat kartu di funnel CRM saat lead sudah hangat"),
       width="150px"),
])

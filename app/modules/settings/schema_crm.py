"""CRM settings — the section, plus the rule that they are never inherited.

The first client's CRM endpoint and the bearer token embedded in its URL lived at the
PLATFORM tier (app_setting rows with branch_id NULL), so every branch resolved them as its
own: any tenant that switched the CRM read-gate on would have started asking a stranger's
CRM about its leads, and any operator with access to a branch could read the token. On top
of that, four of the keys had no editor anywhere in the product, so a branch could not have
overridden them even knowing.

TENANT_ONLY_KEYS is what fixes the first half: SettingRepo drops platform-tier rows for
these keys, so a CRM link only ever comes from the branch that configured it. The fields
below fix the second half. Migration crmtnt00001 moved the two platform rows onto branch 1,
which is the branch that actually uses them (crm_rescue, writeback and the read gate are
all on there) — so branch 1 resolves exactly the same values as before.
"""
from __future__ import annotations

from .fields import SettingSection
from .fields import i18n as _l
from .fields import setting as _f

# Every key naming or authenticating a tenant's CRM. Platform-tier (branch_id NULL) rows
# for these are ignored by the resolver — a CRM belongs to one tenant, never to everyone.
TENANT_ONLY_KEYS: frozenset[str] = frozenset({
    "crm_enabled", "crm_webhook_url",
    "crm_read_enabled", "crm_state_url", "crm_read_secret",
    "crm_mcp_url", "crm_mcp_city_alias",
    "crm_rescue_enabled", "crm_writeback_enabled",
})

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

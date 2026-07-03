"""Settings schema — the single source of truth for every branch setting.

One entry per setting: key, type, default, section, and localized label/placeholder/help.
The settings page renders from this, BranchSettings defaults derive from this, and a new
feature exposes a parameter by adding ONE field here (no scattering across UI/i18n/seed).
"""
from __future__ import annotations

from dataclasses import dataclass

type I18n = dict[str, str]


@dataclass(frozen=True)
class SettingField:
    key: str
    kind: str  # bool | int | text | secret
    default: str
    label: I18n
    placeholder: I18n | None = None
    help: I18n | None = None
    width: str = "120px"
    hidden: bool = False  # in defaults() but never rendered (vestigial/internal keys)


@dataclass(frozen=True)
class SettingSection:
    icon: str
    title: I18n
    fields: list[SettingField]


def _l(ru: str, en: str, id_: str) -> I18n:
    return {"ru": ru, "en": en, "id": id_}


def _f(
    key: str, kind: str, default: str, label: I18n, *,
    ph: I18n | None = None, help: I18n | None = None, width: str = "120px",
    hidden: bool = False,
) -> SettingField:
    return SettingField(key, kind, default, label, ph, help, width, hidden)


_UNLIMITED = _l("0 = без лимита", "0 = unlimited", "0 = tanpa batas")

SCHEMA: list[SettingSection] = [
    SettingSection("fa-solid fa-robot", _l("Бот", "Bot", "Bot"), [
        _f("agent_enabled_global", "bool", "true",
           _l("Авто-ответы бота", "Bot auto-replies", "Balasan otomatis"),
           help=_l("Главный выключатель отправки", "Master send switch", "Sakelar utama"),
           width="130px"),
        _f("reply_delay_min_s", "int", "5",
           _l("Задержка ответа, мин (с)", "Reply delay min (s)", "Jeda min (dtk)"),
           ph=_l("5", "5", "5"), width="110px"),
        _f("reply_delay_max_s", "int", "30",
           _l("Задержка ответа, макс (с)", "Reply delay max (s)", "Jeda maks (dtk)"),
           ph=_l("30", "30", "30"), width="110px"),
        _f("quiet_start", "int", "22",
           _l("Тихие часы с (0–23)", "Quiet from (0–23)", "Tenang dari (0–23)"),
           ph=_l("22", "22", "22"), width="110px"),
        _f("quiet_end", "int", "8",
           _l("Тихие часы до (0–23)", "Quiet to (0–23)", "Tenang sampai (0–23)"),
           ph=_l("8", "8", "8"), width="110px"),
        _f("tz_offset_h", "int", "7",
           _l("Часовой пояс (UTC±)", "Timezone (UTC±)", "Zona waktu (UTC±)"),
           ph=_l("7", "7", "7"), width="110px"),
    ]),
    SettingSection("fa-solid fa-gauge-high",
                   _l("Лимиты · анти-бан", "Limits · anti-ban", "Batas · anti-ban"), [
        _f("hourly_cap", "int", "350",
           _l("Сообщений в час", "Messages / hour", "Pesan / jam"),
           ph=_l("350", "350", "350"), help=_UNLIMITED, width="110px"),
        _f("daily_cap", "int", "2100",
           _l("Сообщений в день", "Messages / day", "Pesan / hari"),
           ph=_l("2100", "2100", "2100"), help=_UNLIMITED, width="110px"),
    ]),
    SettingSection("fa-solid fa-clock-rotate-left",
                   _l("Фолоап", "Follow-up", "Tindak lanjut"), [
        _f("followup_enabled", "bool", "false",
           _l("Включить фолоап", "Enable follow-up", "Aktifkan"), width="130px"),
        _f("followup_schedule_h", "text", "1,4,24,120",
           _l("Расписание (часы)", "Schedule (hours)", "Jadwal (jam)"),
           ph=_l("1,4,24,120", "1,4,24,120", "1,4,24,120"),
           help=_l("Часы после ответа, через запятую",
                   "Hours after reply, comma-separated", "Jam, pisah koma"), width="170px"),
    ]),
    SettingSection("fa-solid fa-brain",
                   _l("Знания и LLM", "Knowledge & LLM", "Pengetahuan & LLM"), [
        _f("knowledge_backend", "text", "direct",
           _l("Движок знаний", "Knowledge backend", "Backend pengetahuan"),
           ph=_l("direct | rag | canary:N", "direct | rag | canary:N", "direct | rag"),
           help=_l("direct — текст · rag — вектор · canary:N — A/B",
                   "direct — text · rag — vector · canary:N — A/B", "direct · rag · canary:N"),
           width="210px"),
        # hidden until the RAG / tech-context / web-search features are ported — the
        # keys are still parsed + seeded, but showing dead toggles misleads the operator.
        _f("tech_usecase_enabled", "bool", "true",
           _l("Кейсы под лида", "Tailor use-cases", "Kasus sesuai lead"),
           width="130px", hidden=True),
        _f("tech_search_enabled", "bool", "false",
           _l("Веб-поиск", "Web search", "Pencarian web"), width="130px", hidden=True),
    ]),
    SettingSection("fa-solid fa-bell",
                   _l("Уведомления", "Notifications", "Notifikasi"), [
        _f("tg_group_id", "text", "",
           _l("Telegram-группа менеджеров", "Manager Telegram group", "Grup Telegram"),
           ph=_l("-1001234567890", "-1001234567890", "-1001234567890"),
           help=_l("ID группы для хэндофф-алертов", "Group id for hand-off alerts",
                   "ID grup untuk alert"), width="210px"),
    ]),
    SettingSection("fa-solid fa-dollar-sign",
                   _l("Бюджет", "Budget", "Anggaran"), [
        _f("daily_budget_usd", "int", "0",
           _l("Дневной лимит LLM, $", "Daily LLM budget, $", "Anggaran LLM, $"),
           ph=_l("10", "10", "10"), help=_UNLIMITED, width="120px"),
    ]),
    SettingSection("fa-solid fa-bullseye",
                   _l("Meta Ads и CAPI", "Meta Ads & CAPI", "Meta Ads & CAPI"), [
        _f("fb_account_id", "text", "", _l("Ad Account ID", "Ad Account ID", "Ad Account ID"),
           ph=_l("act_1234567890", "act_1234567890", "act_1234567890"), width="220px"),
        _f("fb_business_id", "text", "", _l("Business ID", "Business ID", "Business ID"),
           ph=_l("1234567890", "1234567890", "1234567890"), width="220px"),
        _f("meta_pixel_id", "text", "", _l("Pixel ID", "Pixel ID", "Pixel ID"),
           ph=_l("1234567890", "1234567890", "1234567890"), width="220px"),
        _f("meta_capi_token", "secret", "", _l("CAPI токен", "CAPI token", "Token CAPI"),
           ph=_l("EAAB…", "EAAB…", "EAAB…"),
           help=_l("Пусто = не менять", "Blank = keep current", "Kosong = tetap"),
           width="340px"),
    ]),
    SettingSection("fa-solid fa-database", _l("CRM", "CRM", "CRM"), [
        _f("crm_enabled", "bool", "false",
           _l("Слать лиды в CRM", "Send leads to CRM", "Kirim lead ke CRM"), width="130px"),
        _f("crm_webhook_url", "secret", "",
           _l("CRM webhook URL", "CRM webhook URL", "CRM webhook URL"),
           ph=_l("https://…", "https://…", "https://…"),
           help=_l("POST manager_alert на этот URL", "POST manager_alert here",
                   "POST manager_alert ke URL"), width="340px"),
    ]),
]


def tr(d: I18n, lang: str) -> str:
    """Localized string with en fallback then first available."""
    return d.get(lang) or d.get("en") or next(iter(d.values()), "")


def all_fields() -> list[SettingField]:
    return [f for sec in SCHEMA for f in sec.fields]


def defaults() -> dict[str, str]:
    """Key → default for every setting — the source BranchSettings/_DEFAULTS derive from."""
    return {f.key: f.default for f in all_fields()}


def field_for(key: str) -> SettingField | None:
    return next((f for f in all_fields() if f.key == key), None)

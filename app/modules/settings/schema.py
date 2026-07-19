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
    choices: list[tuple[str, I18n]] | None = None  # text field → dropdown of fixed options
    # "branch" renders in the branch panel and applies to the whole branch; "channel" renders
    # in the per-connector editor and resolves per channel (falling back to branch → platform).
    scope: str = "branch"


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
    hidden: bool = False, choices: list[tuple[str, I18n]] | None = None,
    scope: str = "branch",
) -> SettingField:
    return SettingField(key, kind, default, label, ph, help, width, hidden, choices, scope)


_UNLIMITED = _l("0 = без лимита", "0 = unlimited", "0 = tanpa batas")

SCHEMA: list[SettingSection] = [
    SettingSection("fa-solid fa-robot", _l("Бот", "Bot", "Bot"), [
        _f("agent_enabled_global", "bool", "true",
           _l("Авто-ответы бота", "Bot auto-replies", "Balasan otomatis"),
           help=_l("Главный выключатель отправки", "Master send switch", "Sakelar utama"),
           width="130px"),
        _f("reply_delay_min_s", "int", "5",
           _l("Задержка ответа, мин (с)", "Reply delay, min (s)", "Jeda min (dtk)"),
           ph=_l("5", "5", "5"), width="64px", scope="channel"),
        _f("reply_delay_max_s", "int", "30",
           _l("Задержка ответа, макс (с)", "Reply delay, max (s)", "Jeda maks (dtk)"),
           ph=_l("30", "30", "30"), width="64px", scope="channel"),
        _f("quiet_start", "int", "22",
           _l("Тихие часы с (0–23)", "Quiet from (0–23)", "Tenang dari (0–23)"),
           ph=_l("22", "22", "22"), width="64px"),
        _f("quiet_end", "int", "8",
           _l("Тихие часы до (0–23)", "Quiet to (0–23)", "Tenang sampai (0–23)"),
           ph=_l("8", "8", "8"), width="64px"),
        _f("phone_country_code", "text", "62",
           _l("Код страны телефона", "Phone country code", "Kode negara telepon"),
           ph=_l("62", "62", "62"),
           help=_l("Для номеров из текста лида (62=Индонезия, 60=Малайзия, 63=Филиппины)",
                   "For phones in a lead's text (62=Indonesia, 60=Malaysia, 63=Philippines)",
                   "Untuk nomor dari teks lead (62=Indonesia, 60=Malaysia, 63=Filipina)"),
           width="64px", scope="channel"),
    ]),
    SettingSection("fa-solid fa-gauge-high",
                   _l("Лимиты · анти-бан", "Limits · anti-ban", "Batas · anti-ban"), [
        # Defaults sized as a runaway-bug backstop, not a precise anti-ban dial: IG/WhatsApp
        # follow-ups ride the unofficial private APIs (instagrapi/Evolution), where community
        # guidance flags 200+/day as a high-risk bulk-send profile — but this cap also gates
        # safe official-Graph replies to real inbound leads, which run much higher on a busy
        # branch. Too low silently stops replying to real leads for the rest of the window
        # (see the 2026-06-21 "Stepan molchit" incident) — a worse outcome than a modest ban
        # risk. 150/800 gives real headroom over a typical busy branch's organic peak
        # (~50/hour, ~300-500/day) while still being a real ceiling, not the old 350/2100
        # (which never actually triggered).
        _f("hourly_cap", "int", "150",
           _l("Сообщений в час", "Messages / hour", "Pesan / jam"),
           ph=_l("150", "150", "150"), help=_UNLIMITED, width="76px", scope="channel"),
        _f("daily_cap", "int", "800",
           _l("Сообщений в день", "Messages / day", "Pesan / hari"),
           ph=_l("800", "800", "800"), help=_UNLIMITED, width="76px", scope="channel"),
        # Independent from the main bot switch: that one gates scanning incoming + queueing a
        # reply; this one gates the SEND worker draining the queue. Off = keep capturing
        # incoming and queueing replies, but nothing actually goes out — the lever for "the
        # account got soft-blocked, pause sending without losing what comes in".
        _f("sending_enabled", "bool", "true",
           _l("Отправка (исходящие)", "Sending (outbound)", "Pengiriman (keluar)"),
           help=_l(
               "Выкл — очередь копится, но ничего не отправляется (для бана/чекпоинта)",
               "Off — the queue keeps building but nothing sends (for a ban/checkpoint)",
               "Nonaktif — antrean menumpuk tapi tidak terkirim (saat kena banned/checkpoint)"),
           width="90px", scope="channel"),
    ]),
    SettingSection("fa-solid fa-clock-rotate-left",
                   _l("Фолоап", "Follow-up", "Tindak lanjut"), [
        _f("followup_enabled", "bool", "false",
           _l("Включить фолоап", "Enable follow-up", "Aktifkan"), width="130px",
           scope="channel"),
        _f("followup_schedule_h", "text", "1,4,24,120",
           _l("Расписание (часы)", "Schedule (hours)", "Jadwal (jam)"),
           ph=_l("1,4,24,120", "1,4,24,120", "1,4,24,120"),
           help=_l("Часы после ответа, через запятую. У Meta окно ~24ч — ставьте короче",
                   "Hours after reply, comma-separated. Meta's window is ~24h — use shorter",
                   "Jam setelah balasan, pisah koma. Jendela Meta ~24 jam — pakai lebih pendek"),
           width="170px", scope="channel"),
        _f("reactivation_enabled", "bool", "false",
           _l("Реактивация спящих", "Reactivate dormant", "Aktifkan kembali"), width="150px",
           help=_l("Один персональный заход к уснувшим лидам (3-21 дн.), по их же диалогу",
                   "One personalized touch to dormant leads (3-21d), adapted to their dialog",
                   "Satu sapaan personal ke lead yang diam (3-21 hari), sesuai obrolannya")),
        _f("learning_audit_enabled", "bool", "false",
           _l("Еженед. аудит обучения", "Weekly learning audit", "Audit mingguan"),
           width="150px",
           help=_l("Пн 09:00 WIB: авто-разбор недели в TG — нарушения, воронка, предложения. "
                   "Ничего не меняет сам",
                   "Mon 09:00 WIB: weekly self-review to TG - violations, funnel, proposals. "
                   "Changes nothing by itself",
                   "Senin 09:00 WIB: tinjauan mingguan ke TG. Tidak mengubah apa pun")),
    ]),
    SettingSection("fa-solid fa-brain",
                   _l("Знания и LLM", "Knowledge & LLM", "Pengetahuan & LLM"), [
        _f("reply_routing", "text", "hybrid",
           _l("Маршрутизация модели", "Model routing", "Routing model"),
           help=_l("Гибрид: дешёвая модель на простых ходах, сильная — на закрытии сделки",
                   "Hybrid: cheap model on easy turns, strong model for closing",
                   "Hybrid: model murah untuk giliran mudah, model kuat untuk closing"),
           width="170px",
           choices=[
               ("hybrid", _l("Гибрид (экономно)", "Hybrid (thrifty)", "Hybrid (hemat)")),
               ("off", _l("Всегда сильная", "Always strong", "Selalu kuat")),
           ]),
        _f("smart_stages", "multi", "presenting,objection,ready",
           _l("Стадии на сильной модели", "Strong-model stages", "Tahap model kuat"),
           help=_l("Гибрид: отмеченные стадии отвечает сильная модель, остальные — дешёвая. "
                   "Горячие лиды и сигналы оплаты — всегда на сильной. Снять все = дефолт.",
                   "Hybrid: ticked stages use the strong model, the rest use the cheap one. "
                   "Hot leads and payment signals always use strong. Untick all = default.",
                   "Hybrid: tahap tercentang pakai model kuat, sisanya model murah. Lead panas "
                   "& sinyal bayar selalu kuat. Hapus semua = default."),
           width="260px",
           choices=[
               ("new", _l("новый", "new", "baru")),
               ("nurturing", _l("прогрев", "nurturing", "nurturing")),
               ("qualifying", _l("квалиф.", "qualifying", "kualifikasi")),
               ("presenting", _l("презент.", "presenting", "presentasi")),
               ("objection", _l("возраж.", "objection", "keberatan")),
               ("ready", _l("готов", "ready", "siap")),
           ]),
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
           ph=_l("10", "10", "10"), help=_UNLIMITED, width="76px"),
    ]),
    SettingSection("fa-solid fa-bullseye",
                   _l("Коннектор Meta", "Meta connector", "Konektor Meta"), [
        _f("meta_app_id", "text", "", _l("App ID", "App ID", "App ID"),
           ph=_l("1068545755735887", "1068545755735887", "1068545755735887"), width="220px",
           scope="channel"),
        _f("fb_business_id", "text", "", _l("Business ID", "Business ID", "Business ID"),
           ph=_l("1234567890", "1234567890", "1234567890"), width="220px", scope="channel"),
        _f("fb_account_id", "text", "", _l("Ad Account ID", "Ad Account ID", "Ad Account ID"),
           ph=_l("act_1234567890", "act_1234567890", "act_1234567890"), width="220px",
           scope="channel"),
        _f("meta_page_id", "text", "", _l("Page ID", "Page ID", "Page ID"),
           ph=_l("447466948457973", "447466948457973", "447466948457973"), width="220px",
           scope="channel"),
        _f("meta_system_user_token", "secret", "",
           _l("System User токен (реклама + пиксель + сообщения)",
              "System User token (ads + pixel + messaging)",
              "Token System User (iklan + pixel + pesan)"),
           ph=_l("EAAPL…", "EAAPL…", "EAAPL…"),
           help=_l("Единый токен со scope ads_management, ads_read, business_management, "
                   "pages_messaging, pages_read_engagement, pages_show_list, "
                   "instagram_manage_messages. Пусто = не менять",
                   "Single token covering ads_management, ads_read, business_management, "
                   "pages_messaging, pages_read_engagement, pages_show_list, "
                   "instagram_manage_messages. Blank = keep current",
                   "Token tunggal dengan scope ads_management, ads_read, business_management, "
                   "pages_messaging, pages_read_engagement, pages_show_list, "
                   "instagram_manage_messages. Kosong = tetap"),
           width="340px", scope="channel"),
        _f("meta_capi_token", "secret", "", _l("CAPI токен (устар.)", "CAPI token (legacy)",
                                                "Token CAPI (lama)"),
           ph=_l("EAAB…", "EAAB…", "EAAB…"),
           help=_l("Устаревшее поле — используйте System User токен выше. Пусто = не менять",
                   "Legacy field — use the System User token above. Blank = keep current",
                   "Field lama — gunakan token System User di atas. Kosong = tetap"),
           width="340px", hidden=True, scope="channel"),
        _f("meta_ads_token", "secret", "",
           _l("Marketing API токен (устар.)", "Marketing API token (legacy)",
              "Token Marketing API (lama)"),
           ph=_l("EAAG…", "EAAG…", "EAAG…"),
           help=_l("Устаревшее поле — используйте System User токен выше. Пусто = не менять",
                   "Legacy field — use the System User token above. Blank = keep current",
                   "Field lama — gunakan token System User di atas. Kosong = tetap"),
           width="340px", hidden=True, scope="channel"),
    ]),
    SettingSection("fa-solid fa-chart-line",
                   _l("Meta — доп. опция: пиксель (CAPI)", "Meta — add-on: pixel (CAPI)",
                      "Meta — opsi tambahan: pixel (CAPI)"), [
        _f("meta_pixel_send_enabled", "bool", "false",
           _l("Слать события в пиксель", "Send events to pixel", "Kirim event ke pixel"),
           help=_l("Доп. опция поверх коннектора Meta — требует System User токен и Pixel ID "
                   "выше. Выкл. по умолчанию: включайте только когда пиксель настроен и "
                   "проверен.",
                   "Add-on on top of the Meta connector — needs the System User token and "
                   "Pixel ID above. Off by default: enable only once the pixel is configured "
                   "and verified.",
                   "Opsi tambahan di atas konektor Meta — perlu token System User dan Pixel ID "
                   "di atas. Nonaktif secara default: aktifkan hanya setelah pixel diatur dan "
                   "diverifikasi."),
           width="130px", scope="channel"),
        _f("meta_pixel_id", "text", "", _l("Pixel ID", "Pixel ID", "Pixel ID"),
           ph=_l("1234567890", "1234567890", "1234567890"), width="220px", scope="channel"),
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


def channel_keys() -> frozenset[str]:
    """Keys that resolve per connector (scope='channel') — everything else is branch-scoped."""
    return frozenset(f.key for f in all_fields() if f.scope == "channel")


def sections_for_scope(scope: str) -> list[SettingSection]:
    """Sections keeping only the fields of the given scope, dropping now-empty sections —
    lets the branch panel and the per-connector editor render from the same SCHEMA."""
    out: list[SettingSection] = []
    for sec in SCHEMA:
        kept = [f for f in sec.fields if f.scope == scope]
        if kept:
            out.append(SettingSection(sec.icon, sec.title, kept))
    return out

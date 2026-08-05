"""Settings schema — the single source of truth for every branch setting.

One entry per setting: key, type, default, section, and localized label/placeholder/help.
The settings page renders from this, BranchSettings defaults derive from this, and a new
feature exposes a parameter by adding ONE field here (no scattering across UI/i18n/seed).
"""
from __future__ import annotations

from .fields import I18n, SettingField, SettingSection
from .fields import i18n as _l
from .fields import setting as _f
from .schema_crm import CRM_SECTION

__all__ = ["I18n", "SettingField", "SettingSection"]

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
        _f("junk_opener", "text", "",
           _l("Ответ на «привет» без текста", "Reply to a contentless hello",
              "Balasan untuk sapaan kosong"),
           help=_l("Единственный ответ, который пишет не модель: лид прислал эмодзи или "
                   "голое «привет». Пусто — нейтральная фраза без названия компании",
                   "The one reply the model does not write: the lead sent an emoji or a bare "
                   "hello. Empty falls back to a neutral line with no company name",
                   "Satu-satunya balasan yang bukan dari model: lead kirim emoji atau sapaan "
                   "kosong. Kosong = kalimat netral tanpa nama perusahaan"),
           width="100%"),
    ]),
    SettingSection("fa-solid fa-layer-group", _l("Промт", "Prompt", "Prompt"), [
        # Default legacy on purpose: a branch nobody has touched keeps exactly today's prompt,
        # byte for byte. Branch 1 is 37k live messages behind a pinned fingerprint and moves
        # in its own step, deliberately, never as a side effect of somebody else's change.
        #
        # hidden=True — NOT rendered as a dropdown, and that is the whole point. Switching a
        # branch replaces messages[0] wholesale: for branch 1 it would drop 5115 characters of
        # its own selling layer (forms of address, the kampus rule, the MAHAL method, the local
        # evidence the shared no-salary rule leans on), swap which fact document the public
        # fabrication verifier reads, reorder the catalogue, and cold-bust a prompt cache
        # running at a 91% hit rate. Recovery is a second click; the replies sent in between
        # are already on Instagram. That is not a settings toggle, it is a migration with a
        # fingerprint check either side — scripts/prompt_snapshot.py before and after.
        _f("prompt_pipeline", "text", "legacy",
           _l("Сборка промта", "Prompt pipeline", "Perakitan prompt"),
           choices=[("legacy", _l("Наследуемая (общий контракт)", "Legacy (shared contract)",
                                  "Lama (kontrak bersama)")),
                    ("composer", _l("Композер (свои документы + CRAFT)",
                                    "Composer (own docs + CRAFT)",
                                    "Composer (dokumen sendiri + CRAFT)"))],
           help=_l("Композер собирает промт из ДОКУМЕНТОВ филиала (все с флагом «в промт»), "
                   "метод продаж живёт в базе знаний и правится здесь. Наследуемая берёт "
                   "документы по жёсткому списку слагов и общий контракт с индонезийскими "
                   "измерениями",
                   "Composer builds the prompt from the BRANCH's documents (every one flagged "
                   "in-prompt), with the selling method in the knowledge base where it can be "
                   "edited. Legacy loads documents by a hardcoded slug list and ships the "
                   "shared contract with its Indonesian measurements",
                   "Composer menyusun prompt dari DOKUMEN cabang ini; Legacy memakai daftar "
                   "slug tetap dan kontrak bersama"),
           width="200px", hidden=True),
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
    SettingSection("fa-solid fa-comments",
                   _l("Комментарии под постами", "Post comments", "Komentar postingan"), [
        _f("comment_replies_enabled", "bool", "false",
           _l("Отвечать на комментарии", "Reply to comments", "Balas komentar"), width="150px",
           scope="channel",
           help=_l("Раз в час бот собирает новые комментарии под НАШИМИ постами: коротко "
                   "отвечает по делу и уводит тёплых в директ. Публичный ответ строго из базы "
                   "знаний; при сомнении — только приглашение в личку",
                   "Hourly, the bot collects new comments under OUR posts: a short on-topic "
                   "public reply, warm authors invited to DM. Public text strictly from the "
                   "KB; when unsure — a DM invite only",
                   "Tiap jam bot mengumpulkan komentar baru di postingan KAMI: balasan singkat, "
                   "yang hangat diajak ke DM. Teks publik ketat dari basis pengetahuan")),
        # Comment automation is rate-limited by IG far more aggressively than DMs — keep these
        # low and separate from the DM caps. A per-post cap stops the bot carpet-answering one
        # viral post (a fast ban signal).
        _f("comment_hourly_cap", "int", "20",
           _l("Ответов в час", "Replies / hour", "Balasan / jam"),
           ph=_l("20", "20", "20"), width="76px", scope="channel"),
        _f("comment_per_post_cap", "int", "5",
           _l("Ответов на один пост", "Replies / post", "Balasan / post"),
           ph=_l("5", "5", "5"), width="76px", scope="channel"),
    ]),
    SettingSection("fa-solid fa-hand-sparkles",
                   _l("Комментарии под чужими постами", "Comments on other people's posts",
                      "Komentar di postingan orang lain"), [
        _f("proactive_comments_enabled", "bool", "false",
           _l("Напоминать о себе", "Reach out under their posts", "Sapa di postingan mereka"),
           width="150px", scope="channel",
           help=_l("Раз в час бот заходит в ленту тех, кто нам УЖЕ писал, и оставляет одну "
                   "человеческую реплику под свежим постом — без продажи, без ссылок, без "
                   "названия курса. Один человек не чаще раза в 30 дней. Это единственное "
                   "место, где бот пишет первым: держите лимиты низкими",
                   "Hourly, the bot visits the feed of people who ALREADY wrote to us and "
                   "leaves one human line under a recent post — no pitch, no links, no course "
                   "name. One person at most once per 30 days. This is the only place the bot "
                   "speaks first: keep the caps low",
                   "Tiap jam bot mengunjungi feed orang yang SUDAH menulis ke kami dan "
                   "meninggalkan satu komentar manusiawi — tanpa jualan, tanpa tautan")),
        # The judge measures a post against this line. No default and no fallback: a generic
        # "we teach things" would wave through anything cheerful, and the mission simply does
        # not run until a human has written who this branch is.
        _f("proactive_comment_about", "text", "",
           _l("Кто мы и где", "Who we are and where", "Siapa kami dan di mana"),
           ph=_l("курсы программирования и дизайна в Джакарте",
                 "coding and design courses in Jakarta",
                 "kursus coding dan desain di Jakarta"),
           width="320px", scope="channel",
           help=_l("По этой строке ИИ решает, наш это пост или мимо. Пусто — миссия не "
                   "работает",
                   "The AI judges relevance against this line. Empty — the mission does not "
                   "run",
                   "AI menilai relevansi dari kalimat ini. Kosong — misi tidak berjalan")),
        _f("proactive_comment_daily_cap", "int", "5",
           _l("Комментариев в сутки", "Comments / day", "Komentar / hari"),
           ph=_l("5", "5", "5"), width="76px", scope="channel"),
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
    CRM_SECTION,
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


def sections_for_scope(scope: str) -> list[SettingSection]:
    """Sections keeping only the fields of the given scope, dropping now-empty sections —
    lets the branch panel and the per-connector editor render from the same SCHEMA."""
    out: list[SettingSection] = []
    for sec in SCHEMA:
        kept = [f for f in sec.fields if f.scope == scope]
        if kept:
            out.append(SettingSection(sec.icon, sec.title, kept))
    return out

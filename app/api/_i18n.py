"""Request-scoped i18n via ContextVar (same pattern as Stepan-1).

Usage:
    apply_lang(request)   # call once at handler start; sets ContextVar
    t("key")              # anywhere — returns current-lang string
"""
from __future__ import annotations

import contextvars

from starlette.requests import Request

LANG_COOKIE = "stepan2_lang"
LANGS = ("en", "ru", "id")
DEFAULT_LANG = "en"

_lang: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default=DEFAULT_LANG)


def apply_lang(request: Request) -> str:
    """Read lang cookie, set ContextVar, return the active code."""
    raw = request.cookies.get(LANG_COOKIE, DEFAULT_LANG)
    code = raw if raw in LANGS else DEFAULT_LANG
    _lang.set(code)
    return code


def current_lang() -> str:
    return _lang.get()


def t(key: str, **fmt: object) -> str:
    row = _TR.get(key)
    if not row:
        return key
    s = row.get(_lang.get()) or row.get(DEFAULT_LANG) or key
    return s.format(**fmt) if fmt else s


_TR: dict[str, dict[str, str]] = {
    # navigation
    "nav.inbox":    {"ru": "Входящие",    "en": "Inbox",       "id": "Kotak Masuk"},
    "nav.coach":    {"ru": "Коуч KB",     "en": "Coach KB",    "id": "Coach KB"},
    "nav.know":     {"ru": "База знаний", "en": "Knowledge",   "id": "Basis Pengetahuan"},
    "nav.products": {"ru": "Продукты",    "en": "Products",    "id": "Produk"},
    "nav.settings": {"ru": "Настройки",   "en": "Settings",    "id": "Pengaturan"},
    "nav.members":  {"ru": "Участники",   "en": "Members",     "id": "Anggota"},
    "nav.tables":   {"ru": "Таблицы",     "en": "Tables",      "id": "Tabel"},
    # inbox / threads
    "inbox.empty":  {"ru": "Нет чатов",   "en": "No chats",    "id": "Tidak ada obrolan"},
    "inbox.select": {"ru": "Выберите чат","en": "Select a conversation","id": "Pilih percakapan"},
    # chat
    "chat.send":    {"ru": "Отправить",   "en": "Send",        "id": "Kirim"},
    "chat.ph":      {"ru": "Ваше сообщение…","en": "Your message…","id": "Pesan Anda…"},
    "chat.pending": {"ru": "ожидает",     "en": "pending",     "id": "menunggu"},
    "who.agent":    {"ru": "Степан",      "en": "Stepan",      "id": "Stepan"},
    "who.manager":  {"ru": "менеджер",    "en": "manager",     "id": "manajer"},
    "who.lead":     {"ru": "лид",         "en": "lead",        "id": "lead"},
    # coach
    "coach.ph":     {
        "ru": "Что изменить в базе знаний?",
        "en": "What to change in the KB?",
        "id": "Apa yang diubah?",
    },
    "coach.submit":      {"ru": "Предложить правку","en": "Suggest edit","id": "Sarankan edit"},
    "coach.apply":       {"ru": "✓ Применить",      "en": "✓ Apply",     "id": "✓ Terapkan"},
    "coach.cancel":      {"ru": "✗ Отклонить",      "en": "✗ Cancel",    "id": "✗ Batalkan"},
    "coach.hist":        {"ru": "История правок",   "en": "Edit history","id": "Riwayat edit"},
    "coach.rules_title": {
        "ru": "Активные правила для бота",
        "en": "Active bot rules",
        "id": "Aturan bot aktif",
    },
    "coach.no_rules": {"ru": "Правил нет", "en": "No rules set", "id": "Belum ada aturan"},
    # knowledge panel
    "know.title":   {"ru": "Заголовок",   "en": "Title",       "id": "Judul"},
    "know.content": {"ru": "Содержание",  "en": "Content",     "id": "Isi"},
    "know.save":    {"ru": "Сохранить",   "en": "Save",        "id": "Simpan"},
    "know.back":    {"ru": "← База знаний","en": "← Knowledge","id": "← Basis Pengetahuan"},
    "know.saved":   {"ru": "Сохранено ✓", "en": "Saved ✓",    "id": "Tersimpan ✓"},
    # products panel
    "prod.sort_hint": {
        "ru": "Порядок в промпте ИИ: 0 = первый. Чем меньше — тем раньше продукт упоминается.",
        "en": "Order in AI prompt: 0 = first. Lower number = mentioned earlier.",
        "id": "Urutan di prompt AI: 0 = pertama. Angka lebih kecil = lebih awal.",
    },
    # settings panel
    "set.save":     {"ru": "Сохранить", "en": "Save",          "id": "Simpan"},
    "set.saved":    {"ru": "✓",         "en": "✓",             "id": "✓"},
    # help overlay
    "help.title":   {"ru": "Справка",   "en": "Help",          "id": "Bantuan"},
    "help.inbox":   {
        "ru": (
            "Список активных чатов с лидами."
            " Кликните на чат чтобы открыть переписку."
            " Обновляется каждые 30 сек."
        ),
        "en": "All active lead conversations. Click a chat to open. Updates every 30 sec.",
        "id": "Semua percakapan lead aktif. Klik chat untuk membuka. Update tiap 30 detik.",
    },
    "help.coach":   {
        "ru": (
            "Напишите инструкцию для бота — ИИ предложит правку в базе знаний."
            " Применяйте или отклоняйте. Принятые правки вступают в силу сразу."
        ),
        "en": (
            "Type an instruction — AI proposes a KB edit."
            " Apply or decline. Applied edits take effect immediately."
        ),
        "id": (
            "Ketik instruksi — AI mengusulkan edit KB."
            " Terapkan atau tolak. Edit langsung berlaku."
        ),
    },
    "help.know":    {
        "ru": (
            "База знаний бота: persona, FAQ, описания курсов"
            " — всё что Степан знает и цитирует."
            " Кликните на документ чтобы открыть редактор."
        ),
        "en": (
            "Bot knowledge base: persona, FAQ, course info"
            " — everything Stepan knows. Click a doc to edit."
        ),
        "id": "Basis pengetahuan bot: persona, FAQ, info kursus. Klik dokumen untuk mengedit.",
    },
    "help.products": {
        "ru": (
            "Карточки курсов/продуктов. Только активные попадают в ответы бота."
            " Sort = порядок в промпте: 0 — первый, выше число — позже."
        ),
        "en": (
            "Course/product cards. Only active ones appear in bot responses."
            " Sort = order in prompt: 0 = first, higher = later."
        ),
        "id": "Kartu kursus/produk. Hanya yang aktif muncul. Sort = urutan di prompt.",
    },
    "help.members": {
        "ru": (
            "Пользователи и их роли: manager — может отвечать в чатах"
            " и управлять KB, viewer — только просмотр."
        ),
        "en": (
            "Users and roles: manager — can reply in chats and manage KB,"
            " viewer — read-only."
        ),
        "id": "Pengguna dan peran: manager — kelola chat dan KB, viewer — hanya baca.",
    },
    "help.settings": {
        "ru": (
            "Настройки бота для этого филиала."
            " Каждая настройка описана ниже."
            " Сохраняйте кнопкой рядом с полем."
        ),
        "en": (
            "Bot settings for this branch."
            " Each setting is described below."
            " Save with the button next to each field."
        ),
        "id": "Pengaturan bot untuk cabang ini. Setiap pengaturan dijelaskan di bawah.",
    },
    # chat actions
    "chat.stage":       {"ru": "Стадия",           "en": "Stage",             "id": "Tahap"},
    "chat.suggest":     {"ru": "✦ Предложить",     "en": "✦ Suggest",         "id": "✦ Sarankan"},
    "chat.send_stepan": {
        "ru": "Отправить как Стёпан",
        "en": "Send as Stepan",
        "id": "Kirim sebagai Stepan",
    },
    "chat.suggest_ph":  {"ru": "Черновик ответа…", "en": "Draft reply…",     "id": "Draf balasan…"},
    "chat.discard":     {"ru": "✗ Отменить",        "en": "✗ Discard",        "id": "✗ Buang"},
    # leads panel
    "nav.leads":        {"ru": "Лиды",        "en": "Leads",       "id": "Lead"},
    "lead.name":        {"ru": "Имя",         "en": "Name",        "id": "Nama"},
    "lead.phone":       {"ru": "Телефон",     "en": "Phone",       "id": "Telepon"},
    "lead.stage":       {"ru": "Стадия",      "en": "Stage",       "id": "Tahap"},
    "lead.created":     {"ru": "Создан",      "en": "Created",     "id": "Dibuat"},
    "help.leads": {
        "ru": (
            "Все лиды филиала. Один лид = один человек;"
            " написал в IG и WA — один лид, два треда."
            " Стадия обновляется ботом и вручную в чате."
        ),
        "en": (
            "All branch leads. One lead = one person;"
            " IG + WA contact = one lead, two threads."
            " Stage is updated by bot automatically or manually in chat."
        ),
        "id": "Semua lead cabang. Satu lead = satu orang. Tahap diperbarui otomatis oleh bot.",
    },
    # outbox panel
    "nav.outbox":       {"ru": "Исходящие",    "en": "Outbox",      "id": "Kotak Keluar"},
    "outbox.status":    {"ru": "Статус",       "en": "Status",      "id": "Status"},
    "outbox.source":    {"ru": "Источник",     "en": "Source",      "id": "Sumber"},
    "outbox.scheduled": {"ru": "Запланир.",    "en": "Scheduled",   "id": "Dijadwalkan"},
    "help.outbox": {
        "ru": (
            "Очередь исходящих сообщений."
            " Каждое сообщение проходит через неё — caps и rate-limit применяются здесь."
            " source: agent=бот, manager=вы, followup=авто."
        ),
        "en": (
            "Outgoing message queue."
            " Every message passes through here — caps and rate-limits apply here."
            " source: agent=bot, manager=you, followup=auto."
        ),
        "id": "Antrian pesan keluar. Setiap pesan melewati sini. source: agent/manager/followup.",
    },
    # products CRUD
    "prod.create":      {"ru": "+ Продукт",    "en": "+ Product",   "id": "+ Produk"},
    "prod.title_lbl":   {"ru": "Название",     "en": "Title",       "id": "Judul"},
    "prod.slug_lbl":    {"ru": "Slug (ID)",    "en": "Slug (ID)",   "id": "Slug (ID)"},
    "prod.content_lbl": {"ru": "Описание",     "en": "Description", "id": "Deskripsi"},
    "prod.active_lbl":  {"ru": "Активен",      "en": "Active",      "id": "Aktif"},
    "prod.sort_lbl":    {"ru": "Порядок",      "en": "Sort",        "id": "Urutan"},
    "prod.save":        {"ru": "Сохранить",    "en": "Save",        "id": "Simpan"},
    "prod.saved":       {"ru": "Сохранено ✓",  "en": "Saved ✓",    "id": "Tersimpan ✓"},
    "prod.delete":      {"ru": "Удалить",      "en": "Delete",      "id": "Hapus"},
    "prod.back":        {"ru": "← Продукты",   "en": "← Products", "id": "← Produk"},
    # knowledge CRUD
    "know.create":      {"ru": "+ Документ",   "en": "+ Doc",       "id": "+ Dokumen"},
    "know.slug_lbl":    {"ru": "Slug (ID)",    "en": "Slug (ID)",   "id": "Slug (ID)"},
    # coach revert
    "coach.revert":     {"ru": "↩ Откатить",   "en": "↩ Revert",   "id": "↩ Kembalikan"},
    # branch selector
    "branch.filter": {"ru": "Филиал", "en": "Branch", "id": "Cabang"},
    "branch.all":    {"ru": "Все филиалы", "en": "All branches", "id": "Semua cabang"},
    # agent toggle
    "bot.on":        {"ru": "Бот ON",  "en": "Bot ON",  "id": "Bot ON"},
    "bot.off":       {"ru": "Бот OFF", "en": "Bot OFF", "id": "Bot OFF"},
    # stages
    "stage.new":        {"ru": "новый",     "en": "new",          "id": "baru"},
    "stage.qualifying": {"ru": "квалиф.",   "en": "qualifying",   "id": "kualifikasi"},
    "stage.presenting": {"ru": "презент.",  "en": "presenting",   "id": "presentasi"},
    "stage.objection":  {"ru": "возраж.",   "en": "objection",    "id": "keberatan"},
    "stage.ready":      {"ru": "готов",     "en": "ready",        "id": "siap"},
    "stage.handed_off": {"ru": "передан",   "en": "handed off",   "id": "diteruskan"},
    "stage.dormant":    {"ru": "дремлет",   "en": "dormant",      "id": "tidak aktif"},
    "stage.manager":    {"ru": "менеджер",  "en": "manager",      "id": "manajer"},
}

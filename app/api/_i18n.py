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
    "inbox.search": {"ru": "🔍 Поиск по имени / @нику",
                     "en": "🔍 Search name / @handle",
                     "id": "🔍 Cari nama / @handle"},
    "chat.bot_off_hint": {"ru": "Бот выключен для этого лида",
                          "en": "Bot off for this lead",
                          "id": "Bot mati untuk lead ini"},
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
    # captured needs (VPC) in chat header
    "needs.jobs":   {"ru": "Цель",    "en": "Jobs",   "id": "Tujuan"},
    "needs.pains":  {"ru": "Боли",    "en": "Pains",  "id": "Masalah"},
    "needs.gains":  {"ru": "Выгоды",  "en": "Gains",  "id": "Manfaat"},
    # knowledge-base tree / editor / history / reindex
    "kb.tab_persona": {"ru": "Персона",   "en": "Persona",     "id": "Persona"},
    "kb.reindex":   {"ru": "Переиндексировать RAG", "en": "Reindex RAG", "id": "Reindex RAG"},
    "kb.reindexed": {"ru": "Проиндексировано чанков", "en": "Chunks indexed",
                     "id": "Chunk terindeks"},
    "kb.reindex_pick": {"ru": "Выбери филиал в шапке", "en": "Pick a branch first",
                        "id": "Pilih cabang dulu"},
    "kb.preamble":  {"ru": "Вступление",  "en": "Intro",       "id": "Intro"},
    "kb.history":   {"ru": "История",      "en": "History",     "id": "Riwayat"},
    "kb.edited_by": {"ru": "правил:",      "en": "edited by",   "id": "diedit oleh"},
    "kb.restore":   {"ru": "Восстановить", "en": "Restore",     "id": "Pulihkan"},
    "kb.no_history": {"ru": "Правок пока нет", "en": "No edits yet", "id": "Belum ada edit"},
    "kb.back":      {"ru": "Назад",        "en": "Back",        "id": "Kembali"},
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
    # help mode: floating element tips (shown on hover while the ? toggle is on)
    "hint.branch": {
        "ru": "Фильтр филиала: какие чаты и настройки показывать. «Все» — сводно по всем.",
        "en": "Branch filter: which branch's chats and settings to show. 'All' = combined.",
        "id": "Filter cabang: chat & pengaturan cabang mana yang tampil. 'All' = gabungan.",
    },
    "hint.bot_global": {
        "ru": "Главный выключатель Степана: OFF — бот не отвечает никому (синк работает).",
        "en": "Stepan's master switch: OFF — the bot replies to no one (sync keeps running).",
        "id": "Saklar utama Stepan: OFF — bot tidak membalas siapa pun (sync tetap jalan).",
    },
    "hint.lang": {
        "ru": "Язык интерфейса админки. На язык ответов бота не влияет.",
        "en": "Admin UI language. Does not affect the bot's reply language.",
        "id": "Bahasa UI admin. Tidak memengaruhi bahasa balasan bot.",
    },
    "hint.search": {
        "ru": "Поиск по имени или @нику лида в списке чатов.",
        "en": "Search the chat list by lead name or @handle.",
        "id": "Cari daftar chat berdasarkan nama atau @handle lead.",
    },
    "hint.funnel": {
        "ru": "Воронка: количество лидов на каждой стадии. Клик — отфильтровать список чатов.",
        "en": "Funnel: lead count per stage. Click a stage to filter the chat list.",
        "id": "Funnel: jumlah lead per tahap. Klik tahap untuk memfilter daftar chat.",
    },
    "hint.stage": {
        "ru": "Стадия воронки этого лида. Меняется ботом автоматически; можно поправить вручную.",
        "en": "This lead's funnel stage. The bot moves it automatically; override by hand here.",
        "id": "Tahap funnel lead ini. Bot memindahkannya otomatis; bisa diubah manual.",
    },
    "hint.product": {
        "ru": "Курс, который обсуждает лид. Определяет, какие факты из базы попадают в промпт.",
        "en": "The course this lead is discussing. Decides which KB facts enter the prompt.",
        "id": "Kursus yang dibahas lead. Menentukan fakta KB mana yang masuk prompt.",
    },
    "hint.bot_chat": {
        "ru": "Бот в этом чате: OFF — Степан молчит, отвечает только человек.",
        "en": "Bot for THIS chat: OFF — Stepan stays silent, only a human replies.",
        "id": "Bot untuk chat INI: OFF — Stepan diam, hanya manusia yang membalas.",
    },
    "hint.block": {
        "ru": "Заблокировать лида (спам): бот полностью игнорирует все его сообщения.",
        "en": "Block the lead (spam): the bot ignores all their messages entirely.",
        "id": "Blokir lead (spam): bot mengabaikan semua pesannya.",
    },
    "hint.clear_ctx": {
        "ru": "Очистить контекст: старые сообщения сереют и не попадают в промпт Степана.",
        "en": "Clear context: older messages grey out and leave Stepan's prompt.",
        "id": "Bersihkan konteks: pesan lama jadi abu-abu dan keluar dari prompt Stepan.",
    },
    "hint.load_ctx": {
        "ru": "Вернуть контекст: снова включить очищенные сообщения в промпт.",
        "en": "Load context back: cleared messages re-enter the prompt.",
        "id": "Muat ulang konteks: pesan yang dibersihkan masuk prompt lagi.",
    },
    "hint.needs": {
        "ru": "Что Степан выяснил о лиде: цели (jobs), боли (pains), желаемое (gains).",
        "en": "What Stepan discovered: goals (jobs), fears (pains), desired outcomes (gains).",
        "id": "Yang Stepan temukan: tujuan (jobs), kendala (pains), hasil diinginkan (gains).",
    },
    "hint.suggest": {
        "ru": "ИИ пишет черновик ответа. Правьте и отправляйте — сам он не уйдёт.",
        "en": "AI drafts a reply. Edit and send it yourself — it never sends on its own.",
        "id": "AI membuat draf balasan. Edit dan kirim sendiri — tidak terkirim otomatis.",
    },
    "hint.summary": {
        "ru": "Саммари всего чата на языке интерфейса. Повторный клик — скрыть.",
        "en": "Summary of the whole chat in the UI language. Click again to hide.",
        "id": "Ringkasan seluruh chat dalam bahasa UI. Klik lagi untuk sembunyikan.",
    },
    "hint.composer": {
        "ru": "Ваш ответ лиду от имени аккаунта. Enter — отправить, Shift+Enter — новая строка.",
        "en": "Your reply to the lead from the account. Enter sends, Shift+Enter = new line.",
        "id": "Balasan Anda ke lead dari akun. Enter kirim, Shift+Enter baris baru.",
    },
    # chat actions
    "chat.stage":       {"ru": "Стадия",           "en": "Stage",             "id": "Tahap"},
    "chat.suggest":     {"ru": "✦ Предложить",     "en": "✦ Suggest",         "id": "✦ Sarankan"},
    "chat.translate":   {"ru": "≡ Саммари",        "en": "≡ Summary",         "id": "≡ Ringkasan"},
    "chat.tr_result":   {"ru": "Саммари:", "en": "Summary:", "id": "Ringkasan:"},
    "rep.ad_funnel":    {"ru": "Воронка по рекламе", "en": "Ad funnel", "id": "Corong iklan"},
    "rep.ad":           {"ru": "Реклама", "en": "Ad", "id": "Iklan"},
    "chat.block":       {"ru": "Заблокировать (спам)", "en": "Block (spam)",
                         "id": "Blokir (spam)"},
    "chat.blocked":     {"ru": "заблок.", "en": "blocked", "id": "diblokir"},
    "chat.clear":       {"ru": "Очистить контекст", "en": "Clear context",
                         "id": "Bersihkan konteks"},
    "chat.clear_confirm": {"ru": "Очистить контекст диалога?",
                           "en": "Clear conversation context?", "id": "Bersihkan konteks?"},
    "chat.cleared":     {"ru": "Контекст очищен", "en": "Context cleared",
                         "id": "Konteks dibersihkan"},
    "chat.load_ctx":    {"ru": "Загрузить весь контекст", "en": "Load full context",
                         "id": "Muat semua konteks"},
    "chat.del_confirm": {"ru": "Удалить?", "en": "Delete?", "id": "Hapus?"},
    "chat.loaded":      {"ru": "Контекст загружен", "en": "Context loaded",
                         "id": "Konteks dimuat"},
    "log.stage_change": {"ru": "Стадия: {from} → {to}", "en": "Stage: {from} → {to}",
                         "id": "Tahap: {from} → {to}"},
    "product.none":     {"ru": "— без продукта —", "en": "— no product —",
                         "id": "— tanpa produk —"},
    "chat.product":     {"ru": "Продукт изменён", "en": "Product changed",
                         "id": "Produk diubah"},
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
    "outbox.chat":      {"ru": "Чат",          "en": "Chat",        "id": "Chat"},
    "outbox.sent":      {"ru": "Отправлено",   "en": "Sent",        "id": "Terkirim"},
    # broker log page
    "nav.log":          {"ru": "Лог брокера",  "en": "Broker log",  "id": "Log broker"},
    "log.title":        {"ru": "Лог вызовов брокера", "en": "Broker call log",
                         "id": "Log panggilan broker"},
    "log.intro": {
        "ru": "Каждый вызов брокера — ответы, follow-up, перевод, эмбеддинг, coach. "
              "request_id — для сверки с брокером.",
        "en": "Every broker call — replies, follow-ups, translation, embedding, coach. "
              "request_id maps to the broker's own log.",
        "id": "Setiap panggilan broker — balasan, follow-up, terjemahan, embedding, coach.",
    },
    "log.when":         {"ru": "Время",        "en": "Time",        "id": "Waktu"},
    "log.kind":         {"ru": "Тип",          "en": "Kind",        "id": "Jenis"},
    "log.chat":         {"ru": "Чат",          "en": "Chat",        "id": "Chat"},
    "log.model":        {"ru": "Модель",       "en": "Model",       "id": "Model"},
    "log.cost":         {"ru": "Цена",         "en": "Cost",        "id": "Biaya"},
    "log.dur":          {"ru": "Время",        "en": "Latency",     "id": "Durasi"},
    "log.empty":        {"ru": "Пока пусто",   "en": "No calls yet", "id": "Belum ada"},
    "log.prev":         {"ru": "Назад",        "en": "Prev",        "id": "Sebelumnya"},
    "log.next":         {"ru": "Вперёд",       "en": "Next",        "id": "Berikutnya"},
    "log.page":         {"ru": "Стр.",         "en": "Page",        "id": "Hal."},
    "log.total":        {"ru": "всего",        "en": "total",       "id": "total"},
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
    "know.select":  {"ru": "Выберите документ", "en": "Select a document", "id": "Pilih dokumen"},
    "know.new_doc": {"ru": "Новый документ",    "en": "New Document",      "id": "Dokumen Baru"},
    # coach revert
    "coach.revert":     {"ru": "↩ Откатить",   "en": "↩ Revert",   "id": "↩ Kembalikan"},
    # channel management
    "ch.title":    {"ru": "Каналы",            "en": "Channels",          "id": "Kanal"},
    "ch.add":      {"ru": "+ Канал",            "en": "+ Channel",         "id": "+ Kanal"},
    "ch.kind":     {"ru": "Тип",               "en": "Type",              "id": "Jenis"},
    "ch.handle":   {"ru": "Аккаунт",           "en": "Account",           "id": "Akun"},
    "ch.edit":     {"ru": "Ред.",              "en": "Edit",              "id": "Edit"},
    "ch.connect":  {"ru": "Подключить",        "en": "Connect",           "id": "Hubungkan"},
    "ch.delete":   {"ru": "Удалить",           "en": "Delete",            "id": "Hapus"},
    "ch.save":     {"ru": "Сохранить",         "en": "Save",              "id": "Simpan"},
    "ch.active":   {"ru": "активен",           "en": "active",            "id": "aktif"},
    "ch.verify":   {"ru": "Подтвердить",       "en": "Verify",            "id": "Verifikasi"},
    "ch.ig_login": {"ru": "Войти в Instagram", "en": "Login Instagram",   "id": "Login Instagram"},
    "ch.username": {"ru": "Имя пользователя",  "en": "Username",          "id": "Username"},
    "ch.password": {"ru": "Пароль",            "en": "Password",          "id": "Kata sandi"},
    "ch.code_2fa": {"ru": "Код 2FA",           "en": "2FA Code",          "id": "Kode 2FA"},
    "ch.token":    {"ru": "Access Token",      "en": "Access Token",      "id": "Access Token"},
    "ch.page_id":  {"ru": "Page / Account ID", "en": "Page / Account ID",
                    "id": "Page / Account ID"},
    "ch.wa_url":   {"ru": "URL Evolution API", "en": "Evolution API URL",
                    "id": "URL Evolution API"},
    "ch.wa_inst":  {"ru": "Instance",       "en": "Instance",  "id": "Instance"},
    "ch.wa_key":   {"ru": "API ключ",       "en": "API Key",   "id": "Kunci API"},
    "ch.ig_json":  {"ru": "Session JSON",   "en": "Session JSON", "id": "Session JSON"},
    "ch.no_ch":    {"ru": "Нет каналов",    "en": "No channels",  "id": "Belum ada kanal"},
    "ch.new":      {"ru": "Новый канал",    "en": "New Channel",  "id": "Kanal Baru"},
    "ch.kind_ig":  {"ru": "Instagram (instagrapi)",
                    "en": "Instagram (instagrapi)", "id": "Instagram (instagrapi)"},
    "ch.kind_meta":{"ru": "Meta Business (Graph API)",
                    "en": "Meta Business (Graph API)", "id": "Meta Business"},
    "ch.kind_wa":  {"ru": "WhatsApp (Evolution API)",
                    "en": "WhatsApp (Evolution API)", "id": "WhatsApp"},
    "ch.st_active":{"ru": "активен",           "en": "active",            "id": "aktif"},
    "ch.st_exp":   {"ru": "истёк",             "en": "expired",           "id": "kedaluwarsa"},
    "ch.st_chal":  {"ru": "требует входа",     "en": "challenge",         "id": "perlu login"},
    "ch.st_none":  {"ru": "не подключён",      "en": "not connected",     "id": "belum terhubung"},
    "ch.session_ok": {"ru": "Сессия активна — канал подключён.",
                      "en": "Session active — channel connected.",
                      "id": "Sesi aktif — kanal terhubung."},
    "ch.reconnect": {"ru": "Переподключить",   "en": "Reconnect",         "id": "Sambungkan ulang"},
    "ch.logging_in": {"ru": "входим в Instagram… (до 30 сек)",
                      "en": "logging in to Instagram… (up to 30s)",
                      "id": "masuk ke Instagram… (hingga 30 dtk)"},
    "ch.or_login": {"ru": "— или войти напрямую —",
                    "en": "— or login directly —", "id": "— atau login langsung —"},
    # branch selector
    "branch.filter": {"ru": "Филиал", "en": "Branch", "id": "Cabang"},
    "branch.all":    {"ru": "Все филиалы", "en": "All branches", "id": "Semua cabang"},
    # branch management panel
    "nav.branches":  {"ru": "Филиалы",      "en": "Branches",    "id": "Cabang"},
    "br.name":       {"ru": "Название",     "en": "Name",        "id": "Nama"},
    "br.lang_lbl":   {"ru": "Язык бота",   "en": "Bot language","id": "Bahasa bot"},
    "br.tz":         {"ru": "Часовой пояс",  "en": "Time zone",   "id": "Zona waktu"},
    "br.active":     {"ru": "Активен",     "en": "Active",       "id": "Aktif"},
    "br.create":     {"ru": "+ Филиал",    "en": "+ Branch",     "id": "+ Cabang"},
    "br.new":        {"ru": "Новый филиал","en": "New Branch",   "id": "Cabang Baru"},
    "br.edit_title": {"ru": "Редактировать филиал","en": "Edit Branch","id": "Edit Cabang"},
    "br.edit":       {"ru": "Изменить",    "en": "Edit",         "id": "Edit"},
    "br.save":       {"ru": "Сохранить",   "en": "Save",         "id": "Simpan"},
    "br.back":           {"ru": "← Филиалы",  "en": "← Branches",   "id": "← Cabang"},
    "br.settings_seeded": {
        "ru": "Настройки бота засеяны по умолчанию.",
        "en": "Default bot settings have been seeded automatically.",
        "id": "Pengaturan bot default telah ditambahkan otomatis.",
    },
    "help.branches": {
        "ru": (
            "Управление филиалами."
            " Каждый филиал — отдельный арендатор: своя KB, продукты и настройки бота."
            " При создании нового филиала настройки добавляются автоматически."
            " Язык влияет на то, на каком языке бот ведёт переписку."
        ),
        "en": (
            "Manage branches."
            " Each branch is a separate tenant with its own KB, products, and bot settings."
            " Default settings are seeded automatically when a branch is created."
            " Language controls the bot's conversation language."
        ),
        "id": (
            "Kelola cabang."
            " Setiap cabang adalah tenant terpisah dengan KB, produk, dan pengaturan bot sendiri."
            " Pengaturan default ditambahkan otomatis saat cabang dibuat."
        ),
    },
    # agent toggle
    "bot.on":        {"ru": "ON",  "en": "ON",  "id": "ON"},
    "bot.off":       {"ru": "OFF", "en": "OFF", "id": "OFF"},
    "bot.platform":  {"ru": "Степан — вся платформа", "en": "Stepan — whole platform",
                      "id": "Stepan — semua"},
    "bot.branch":    {"ru": "Степан — этот филиал", "en": "Stepan — this branch",
                      "id": "Stepan — cabang ini"},
    "bot.pick_branch": {"ru": "Выберите один филиал в фильтре, чтобы управлять его ботом",
                        "en": "Pick a single branch in the filter to control its bot",
                        "id": "Pilih satu cabang di filter untuk mengatur botnya"},
    # time abbreviations
    "time.m":        {"ru": "м",  "en": "m",  "id": "m"},
    "time.h":        {"ru": "ч",  "en": "h",  "id": "j"},
    "time.d":        {"ru": "д",  "en": "d",  "id": "h"},
    # stages
    "stage.new":        {"ru": "новый",     "en": "new",          "id": "baru"},
    "stage.nurturing":  {"ru": "прогрев",   "en": "nurturing",    "id": "nurturing"},
    "stage.qualifying": {"ru": "квалиф.",   "en": "qualifying",   "id": "kualifikasi"},
    "stage.presenting": {"ru": "презент.",  "en": "presenting",   "id": "presentasi"},
    "stage.objection":  {"ru": "возраж.",   "en": "objection",    "id": "keberatan"},
    "stage.ready":      {"ru": "готов",     "en": "ready",        "id": "siap"},
    "stage.handed_off": {"ru": "передан",   "en": "handed off",   "id": "diteruskan"},
    "stage.dormant":    {"ru": "дремлет",   "en": "dormant",      "id": "tidak aktif"},
    "stage.manager":    {"ru": "менеджер",  "en": "manager",      "id": "manajer"},
    # funnel widget
    "fnl.title":   {"ru": "ВОРОНКА",        "en": "FUNNEL",       "id": "CORONG"},
    "fnl.total":   {"ru": "Всего",          "en": "Total",        "id": "Total"},
    "fnl.all":     {"ru": "все",            "en": "all",          "id": "semua"},
    "fnl.bot_on":  {"ru": "бот вкл",        "en": "bot on",       "id": "bot aktif"},
    "fnl.in_funnel": {"ru": "в воронке",    "en": "in funnel",    "id": "di corong"},
    "fnl.blocked": {"ru": "заблокированные", "en": "blocked",    "id": "diblokir"},
    # reports
    "nav.reports": {"ru": "Отчёты",         "en": "Reports",      "id": "Laporan"},
    "rep.title":   {"ru": "Отчёты",         "en": "Reports",      "id": "Laporan"},
    "rep.total":   {"ru": "Всего лидов",    "en": "Total leads",  "id": "Total lead"},
    "rep.pipeline":{"ru": "В работе",       "en": "Pipeline",     "id": "Pipeline"},
    "rep.won":     {"ru": "Закрытые",       "en": "Won",          "id": "Berhasil"},
    "rep.conv":    {"ru": "Конверсия",      "en": "Conversion",   "id": "Konversi"},
    "rep.dormant": {"ru": "Спящие",         "en": "Dormant",      "id": "Tidak aktif"},
    "rep.discovered": {"ru": "Выявлено до презент.", "en": "Discovered 1st",
                       "id": "Gali dulu"},
    "rep.disc_len": {"ru": "Ср. глубина выявл.", "en": "Avg discovery msgs",
                     "id": "Rata2 gali"},
    "rep.funnel":  {"ru": "Воронка продаж",  "en": "Sales funnel", "id": "Corong penjualan"},
    "rep.from":    {"ru": "С даты",          "en": "From",         "id": "Dari"},
    "rep.to":      {"ru": "По дату",         "en": "To",           "id": "Sampai"},
    "rep.apply":   {"ru": "Показать",        "en": "Apply",        "id": "Terapkan"},
    "rep.date_hint": {"ru": "Дата = старт диалога с лидом",
                      "en": "Date = when the lead conversation started",
                      "id": "Tanggal = mulai percakapan lead"},
    # how each funnel stage is determined (tooltip on each step)
    "sdesc.new":        {"ru": "Лид только пришёл, содержательного ответа бота ещё не было",
                         "en": "Lead just arrived, no substantive bot reply yet",
                         "id": "Lead baru masuk, belum ada balasan berarti"},
    "sdesc.nurturing":  {"ru": "Холодный: ещё не решил, что IT для него — бот греет интерес",
                         "en": "Cold/curious: not sold on IT yet, bot warms interest",
                         "id": "Dingin: belum yakin IT — bot menghangatkan minat"},
    "sdesc.qualifying": {"ru": "Выявление потребности (SPIN): копаем боль и цель до презентации",
                         "en": "Discovering the need (SPIN): dig pain+goal before pitching",
                         "id": "Menggali kebutuhan (SPIN): gali sebelum presentasi"},
    "sdesc.presenting": {"ru": "Потребность captured — бот презентует продукт под неё",
                         "en": "Need captured — bot presents the product against it",
                         "id": "Kebutuhan tercatat — bot mempresentasikan produk"},
    "sdesc.objection":  {"ru": "Лид возражает (цена/сомнения) — бот честно снимает возражение",
                         "en": "Lead objects (price/doubt) — bot handles it honestly",
                         "id": "Lead keberatan (harga/ragu) — bot menangani jujur"},
    "sdesc.ready":      {"ru": "Лид дал контакт / готов записаться — целевое действие",
                         "en": "Lead gave a contact / ready to enrol — the goal",
                         "id": "Lead beri kontak / siap daftar — target"},
    "sdesc.handed_off": {"ru": "Передан живой команде (нужен человек)",
                         "en": "Handed to the human team (needs a person)",
                         "id": "Diteruskan ke tim (butuh manusia)"},
    "sdesc.dormant":    {"ru": "Замолчал, фолоапы исчерпаны — спящий",
                         "en": "Went silent, follow-ups exhausted — dormant",
                         "id": "Diam, follow-up habis — tidak aktif"},
    "sdesc.manager":    {"ru": "Менеджер взял диалог на себя (бот молчит)",
                         "en": "A manager took over the chat (bot stays silent)",
                         "id": "Manajer mengambil alih (bot diam)"},
    "rep.by_stage":{"ru": "По стадиям",     "en": "By stage",     "id": "Per tahap"},
    "rep.stage":   {"ru": "Стадия",         "en": "Stage",        "id": "Tahap"},
    "rep.count":   {"ru": "Кол-во",         "en": "Count",        "id": "Jumlah"},
    "rep.activity":{"ru": "Сообщения за период", "en": "Messages in period",
                    "id": "Pesan dalam periode"},
    "rep.msgs_in": {"ru": "входящие",       "en": "incoming",     "id": "masuk"},
    "rep.msgs_out":{"ru": "исходящие",      "en": "outgoing",     "id": "keluar"},
    "rep.msgs_total": {"ru": "всего",       "en": "total",        "id": "total"},
    "rep.by_hour": {"ru": "по часам (0-23)", "en": "by hour (0-23)", "id": "per jam (0-23)"},
    "help.reports": {
        "ru": (
            "Общая статистика: воронка по стадиям, конверсия, активность по часам."
            " Обновляется при каждом открытии."
        ),
        "en": (
            "Overall stats: stage funnel, conversion rate, hourly activity."
            " Refreshes on every open."
        ),
        "id": "Statistik: corong tahap, konversi, aktivitas per jam.",
    },
}

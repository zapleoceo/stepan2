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
    "inbox.ad_filter": {"ru": "Чаты по рекламе", "en": "Chats from ad", "id": "Chat dari iklan"},
    "inbox.seg_filter": {"ru": "Сегмент", "en": "Segment", "id": "Segmen"},
    "inbox.ad_clear": {"ru": "Показать все чаты", "en": "Show all chats",
                       "id": "Tampilkan semua chat"},
    "inbox.awaiting_tip": {"ru": "Чатов без ответа Степана — открыть очередь",
                           "en": "Chats awaiting Stepan's reply — open the queue",
                           "id": "Chat menunggu balasan Stepan — buka antrian"},
    "inbox.awaiting_filter": {"ru": "Ждут ответа", "en": "Awaiting reply",
                              "id": "Menunggu balasan"},
    "inbox.await_queue": {"ru": "Без ответа, Стёпа в работе (активная стадия воронки)",
                          "en": "Unanswered, in Stepan's active queue",
                          "id": "Tanpa balasan, dalam antrean aktif Stepan"},
    "inbox.await_off": {"ru": "Без ответа, вне работы Стёпы (спящие / переданные / выключен)",
                        "en": "Unanswered, outside Stepan's queue (dormant / handed off / bot off)",
                        "id": "Tanpa balasan, di luar antrean Stepan"},
    "chat.bot_off_hint": {"ru": "Бот выключен для этого лида",
                          "en": "Bot off for this lead",
                          "id": "Bot mati untuk lead ini"},
    # chat
    "chat.send":    {"ru": "Отправить",   "en": "Send",        "id": "Kirim"},
    "chat.ph":      {"ru": "Ваше сообщение…","en": "Your message…","id": "Pesan Anda…"},
    "chat.pending": {"ru": "ожидает",     "en": "pending",     "id": "menunggu"},
    "chat.send_failed": {"ru": "не отправлено", "en": "not sent", "id": "tidak terkirim"},
    "chat.retry":   {"ru": "Повторить",   "en": "Retry",       "id": "Ulangi"},
    "chat.dismiss": {"ru": "Убрать",      "en": "Dismiss",     "id": "Sembunyikan"},
    "who.agent":    {"ru": "Степан",      "en": "Stepan",      "id": "Stepan"},
    "who.manager":  {"ru": "менеджер",    "en": "manager",     "id": "manajer"},
    "who.lead":     {"ru": "лид",         "en": "lead",        "id": "lead"},
    # coach
    "coach.ph":     {
        "ru": "Спросите про базу или продиктуйте, что добавить/изменить…",
        "en": "Ask about the KB, or dictate what to add/change…",
        "id": "Tanya soal KB, atau diktekan apa yang ditambah/diubah…",
    },
    "coach.thinking": {"ru": "Степан думает…", "en": "Stepan is thinking…",
                       "id": "Stepan sedang berpikir…"},
    "coach.think1": {"ru": "Читаю всю базу знаний…", "en": "Reading the whole knowledge base…",
                     "id": "Membaca seluruh basis pengetahuan…"},
    "coach.think2": {"ru": "Анализирую документы…", "en": "Analysing the documents…",
                     "id": "Menganalisis dokumen…"},
    "coach.think3": {"ru": "Ищу, где ответ или куда добавить…",
                     "en": "Finding the answer / where to add…",
                     "id": "Mencari jawaban / tempat menambah…"},
    "coach.think4": {"ru": "Формулирую…", "en": "Composing…", "id": "Menyusun…"},
    "coach.st.answered": {"ru": "ответ",       "en": "answer",      "id": "jawaban"},
    "coach.st.thinking": {"ru": "думает…",     "en": "thinking…",   "id": "memproses…"},
    "coach.generating":  {"ru": "Готовлю ответ…", "en": "Generating…", "id": "Menyiapkan…"},
    "coach.st.clarify":  {"ru": "уточнение",   "en": "clarify",     "id": "klarifikasi"},
    "coach.st.proposed": {"ru": "предложено",  "en": "proposed",    "id": "diusulkan"},
    "coach.st.applied":  {"ru": "применено",   "en": "applied",     "id": "diterapkan"},
    "coach.st.cancelled":{"ru": "отклонено",   "en": "cancelled",   "id": "dibatalkan"},
    "coach.st.failed":   {"ru": "не найдено",  "en": "not found",   "id": "tidak ditemukan"},
    "coach.st.reverted": {"ru": "откачено",    "en": "reverted",    "id": "dikembalikan"},
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
    "cloud.title":  {"ru": "Облако потребностей лидов",
                     "en": "Lead needs cloud", "id": "Awan kebutuhan lead"},
    "cloud.empty":  {"ru": "пока нет данных", "en": "no data yet", "id": "belum ada data"},
    "cloud.pains":  {"ru": "Боли",   "en": "Pains", "id": "Masalah"},
    "cloud.jobs":   {"ru": "Цели",   "en": "Goals", "id": "Tujuan"},
    "cloud.gains":  {"ru": "Выгоды", "en": "Gains", "id": "Manfaat"},
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
    "set.autosave": {"ru": "сохраняется автоматически", "en": "saves automatically",
                     "id": "tersimpan otomatis"},
    "set.cap_reached": {"ru": "лимит исчерпан, отправка на паузе",
                        "en": "cap reached, sends paused", "id": "batas tercapai, jeda kirim"},
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
            "Пользователи и их роли/филиалы. Видно и редактируется только"
            " супер-админом."
        ),
        "en": "Users, their roles and branches. Visible and editable to super admins only.",
        "id": "Pengguna, peran, dan cabang. Hanya terlihat/diedit oleh super admin.",
    },
    "role.super_admin":  {"ru": "Супер-админ",   "en": "Super admin",  "id": "Super admin"},
    "role.branch_admin": {"ru": "Админ филиала", "en": "Branch admin", "id": "Admin cabang"},
    "role.branch_viewer": {"ru": "Наблюдатель",  "en": "Viewer",       "id": "Peninjau"},
    "member.add":        {"ru": "Добавить",       "en": "Add",         "id": "Tambah"},
    "member.tg_id":      {"ru": "Telegram ID",    "en": "Telegram ID", "id": "Telegram ID"},
    "member.name":       {"ru": "Имя",            "en": "Name",        "id": "Nama"},
    "member.remove":     {"ru": "Удалить",        "en": "Remove",      "id": "Hapus"},
    "member.remove_confirm": {
        "ru": "Убрать этого участника?", "en": "Remove this member?", "id": "Hapus anggota ini?",
    },
    "member.platform":   {"ru": "— (вся платформа)", "en": "— (whole platform)",
                          "id": "— (seluruh platform)"},
    "member.self_locked": {
        "ru": "Нельзя редактировать себя здесь",
        "en": "Can't edit yourself here",
        "id": "Tidak bisa mengedit diri sendiri di sini",
    },
    "help.settings": {
        "ru": (
            "Настройки бота для этого филиала."
            " Каждая настройка описана ниже."
            " Изменения сохраняются автоматически — отдельной кнопки нет."
        ),
        "en": (
            "Bot settings for this branch."
            " Each setting is described below."
            " Changes save automatically — there is no separate button."
        ),
        "id": (
            "Pengaturan bot untuk cabang ini. Setiap pengaturan dijelaskan di bawah."
            " Perubahan tersimpan otomatis — tidak ada tombol terpisah."
        ),
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
    "hint.sending_global": {
        "ru": "Отправка: OFF — очередь копится, но ничего не уходит (для бана/чекпоинта).",
        "en": "Sending: OFF — the queue keeps building, nothing goes out (for a ban/checkpoint).",
        "id": "Pengiriman: OFF — antrean menumpuk, tidak ada yang terkirim (saat banned).",
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
    "hint.kind_filter": {
        "ru": "Фильтр по источнику чата. Каждая кнопка включается/выключается отдельно — "
              "показаны чаты включённых коннекторов.",
        "en": "Chat-source filter. Each button toggles on/off independently — the list shows "
              "chats from the enabled connectors.",
        "id": "Filter sumber chat. Tiap tombol bisa dinyalakan/dimatikan sendiri.",
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
    "hint.manager_note": {
        "ru": "Личная заметка ЭТОМУ лиду (не всему филиалу) — Степан видит её на каждом "
              "ходу, пока не очистишь. Напр.: «проверил, не готов — не считай ready снова "
              "без нового сигнала».",
        "en": "A note for THIS lead only (not the whole branch) — Stepan sees it every turn "
              "until cleared. E.g.: 'checked, not ready yet — needs a fresh signal before ready.'",
        "id": "Catatan khusus lead INI (bukan seluruh cabang) — Stepan melihatnya tiap giliran "
              "sampai dihapus.",
    },
    "chat.manager_note_ph": {
        "ru": "Заметка для Степана по этому лиду (необязательно)…",
        "en": "Note for Stepan on this lead (optional)…",
        "id": "Catatan untuk Stepan tentang lead ini (opsional)…",
    },
    "chat.save": {
        "ru": "Сохранить",
        "en": "Save",
        "id": "Simpan",
    },
    "chat.skip": {
        "ru": "Пропустить",
        "en": "Skip",
        "id": "Lewati",
    },
    "chat.stage_reason_title": {
        "ru": "Причина смены стадии (необязательно) — Степан увидит это",
        "en": "Reason for the stage change (optional) — Stepan will see this",
        "id": "Alasan perubahan tahap (opsional) — Stepan akan melihatnya",
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
    "chat.analyze":     {"ru": "Разбор",           "en": "Analyze",           "id": "Analisa"},
    "coach.analysis":   {"ru": "Разбор чата (сверка с базой)", "en": "Chat analysis (vs KB)",
                         "id": "Analisa chat (vs KB)"},
    "hint.analyze": {
        "ru": "Коуч читает весь чат и сверяет с базой: где бот ответил верно/неверно и чего "
              "в базе не хватило.",
        "en": "Coach reads the whole chat vs the KB: where the bot was right/wrong and what "
              "the KB was missing.",
        "id": "Coach membaca seluruh chat vs KB: di mana bot benar/salah dan apa yang kurang.",
    },
    "chat.tr_result":   {"ru": "Саммари:", "en": "Summary:", "id": "Ringkasan:"},
    "rep.ad_funnel":    {"ru": "Воронка по рекламе", "en": "Ad funnel", "id": "Corong iklan"},
    "rep.ad":           {"ru": "Реклама", "en": "Ad", "id": "Iklan"},
    "rep.ad_spend":     {"ru": "Реклама и расход (Meta)", "en": "Ad spend (Meta)",
                         "id": "Belanja iklan (Meta)"},
    "rep.ad_tree":      {"ru": "Кампании: расход и воронка",
                         "en": "Campaigns: spend and funnel",
                         "id": "Kampanye: belanja dan corong"},
    "rep.ads_unmatched": {"ru": "Без привязки к рекламе", "en": "Not matched to an ad",
                          "id": "Tidak cocok dengan iklan"},
    "rep.ads_no_spend": {"ru": "расход неизвестен", "en": "spend unknown",
                         "id": "belanja tidak diketahui"},
    "rep.ads_campaign": {"ru": "Кампания", "en": "Campaign", "id": "Kampanye"},
    "rep.ads_spend":    {"ru": "Расход", "en": "Spend", "id": "Belanja"},
    "rep.ads_started":  {"ru": "Переписок", "en": "Convos", "id": "Percakapan"},
    "rep.ads_d3":       {"ru": "До 3-го", "en": "Depth 3", "id": "Kedalaman 3"},
    "rep.ads_d5":       {"ru": "До 5-го", "en": "Depth 5", "id": "Kedalaman 5"},
    "rep.ads_leads":    {"ru": "Наших лидов", "en": "Our leads", "id": "Lead kami"},
    "rep.ads_won":      {"ru": "Выиграно", "en": "Won", "id": "Menang"},
    "rep.ads_cpl":      {"ru": "Цена лида", "en": "Cost / lead", "id": "Biaya / lead"},
    "rep.ads_cpw":      {"ru": "Цена выигр.", "en": "Cost / won", "id": "Biaya / menang"},
    "rep.ads_blocks":   {"ru": "Блокировок", "en": "Blocks", "id": "Blokir"},
    "rep.ads_total":    {"ru": "Итого", "en": "Total", "id": "Total"},
    "rep.ads_coverage": {"ru": "Связано с рекламой", "en": "Matched to ads",
                         "id": "Cocok dengan iklan"},
    "rep.ads_synced":   {"ru": "данные на", "en": "synced", "id": "sinkron"},
    "rep.ad_product":   {"ru": "Продукт", "en": "Product", "id": "Produk"},
    "rep.ad_no_product": {"ru": "не задан", "en": "unset", "id": "belum diatur"},
    "rep.ad_suggest_hint": {"ru": "Автоподсказка из истории — клик, чтобы применить",
                            "en": "History suggestion — click to apply",
                            "id": "Saran dari riwayat — klik untuk terapkan"},
    "rep.ad_open_chats": {"ru": "Открыть чаты этой рекламы", "en": "Open this ad's chats",
                          "id": "Buka chat iklan ini"},
    "rep.ad_open_fb":   {"ru": "Открыть объявление в FB", "en": "Open the ad in FB",
                         "id": "Buka iklan di FB"},
    "rep.f_all":        {"ru": "все", "en": "all", "id": "semua"},
    "seg.title":        {"ru": "Сегменты лидов", "en": "Lead segments", "id": "Segmen lead"},
    "seg.hot":          {"ru": "горячие",    "en": "hot",         "id": "panas"},
    "seg.warm":         {"ru": "тёплые",     "en": "warm",        "id": "hangat"},
    "seg.cold":         {"ru": "холодные",   "en": "cold",        "id": "dingin"},
    "seg.no_budget":    {"ru": "без бюджета","en": "no budget",   "id": "tanpa budget"},
    "seg.student":      {"ru": "школьники",  "en": "students",    "id": "pelajar"},
    "seg.non_target":   {"ru": "нецелевые",  "en": "non-target",  "id": "non-target"},
    "seg.unclear":      {"ru": "не ясно",    "en": "unclear",     "id": "belum jelas"},
    "aud.adult":        {"ru": "Взрослые",     "en": "Adults",       "id": "Dewasa"},
    "aud.unknown":      {"ru": "Не определён", "en": "Undetermined", "id": "Belum jelas"},
    "aud.student":      {"ru": "Школьники",    "en": "Students",     "id": "Pelajar"},
    # segment tooltips — HOW each lead_type is decided (mirrors the LEAD TYPE block in
    # app/modules/conversation/prompt.py, the same rules the classifier applies live)
    "segdesc.hot": {
        "ru": "Явное намерение записаться / оплатить / забронировать сейчас "
              "(«как записаться / хочу пойти / как оплатить»)",
        "en": "Explicit intent to enrol / pay / reserve now ('how to sign up / I want in / "
              "how to pay')",
        "id": "Niat jelas untuk daftar / bayar / pesan sekarang ('cara daftar / mau ikut / "
              "gimana bayar')",
    },
    "segdesc.warm": {
        "ru": "Реальный интерес: вовлечён, всплыла настоящая потребность, блокеров нет — "
              "основной путь продажи",
        "en": "Genuine interest: engaged, a real need surfaced, no blocker — the main sell path",
        "id": "Minat nyata: terlibat, kebutuhan muncul, tanpa penghalang — jalur jual utama",
    },
    "segdesc.cold": {
        "ru": "Низкий интерес: размытые или односложные ответы, «просто смотрю / спрашиваю», "
              "без выбранного направления после пары ходов",
        "en": "Low intent: vague or one-word replies, 'just looking / asking', no chosen "
              "direction after a couple of turns",
        "id": "Minat rendah: jawaban samar / satu kata, 'cuma lihat / nanya', tanpa arah "
              "setelah beberapa giliran",
    },
    "segdesc.no_budget": {
        "ru": "Хочет, но не может / не будет платить — «нет денег», шок от цены, нет дохода",
        "en": "Wants it but can't / won't pay — 'no money', price shock, no income",
        "id": "Mau tapi tak bisa / tak mau bayar — 'gapunya duit', kaget harga, tanpa penghasilan",
    },
    "segdesc.student": {
        "ru": "Школьник / несовершеннолетний — целевой сегмент (не блокер): любая программа "
              "со скидкой 10% для школьников, оплата родителями, можно во взрослую группу",
        "en": "School-age / a minor — a target segment (not a blocker): any program at a 10% "
              "student discount, a parent pays, can join the adult group",
        "id": "Usia sekolah / di bawah umur — segmen target (bukan penghalang): program apa pun "
              "diskon pelajar 10%, dibayar orang tua, bisa gabung kelas dewasa",
    },
    "segdesc.non_target": {
        "ru": "Не та аудитория (просит то, чему не учим), оффтоп, троллинг или явное «не хочу»",
        "en": "Wrong audience (asks for something we don't teach), off-topic, trolling, or an "
              "explicit 'I don't want it'",
        "id": "Audiens salah (minta yang tak kami ajar), di luar topik, trolling, atau tegas "
              "'tidak mau'",
    },
    "segdesc.unclear": {
        "ru": "Пока недостаточно сигнала (до ~3 содержательных сообщений) или лид ещё не "
              "классифицирован",
        "en": "Not enough signal yet (under ~3 substantive messages) or the lead is not "
              "classified yet",
        "id": "Sinyal belum cukup (di bawah ~3 pesan berarti) atau lead belum diklasifikasi",
    },
    "seg.tip": {
        "ru": "{label}: {cnt} ({pct}% от всех) · won {won_pct}% (дошли до ready/handed_off). "
              "{desc}",
        "en": "{label}: {cnt} ({pct}% of all) · won {won_pct}% (reached ready/handed_off). {desc}",
        "id": "{label}: {cnt} ({pct}% dari semua) · won {won_pct}% (sampai ready/handed_off). "
              "{desc}",
    },
    "flow.entry_desc": {
        "ru": "Точка входа: уникальные лиды, прошедшие через стартовую стадию (первое "
              "сообщение → первый переход)",
        "en": "Entry point: distinct leads that passed through the starting stage (first "
              "message → first transition)",
        "id": "Titik masuk: lead unik yang melewati tahap awal (pesan pertama → transisi "
              "pertama)",
    },
    "flow.stuck": {"ru": "без движения", "en": "no movement", "id": "tanpa gerakan"},
    "flow.stuck_desc": {
        "ru": "Написали первое сообщение, но ни одного перехода по стадиям ещё не "
              "зафиксировано (совсем свежие или пока не обработаны)",
        "en": "Sent a first message but have no recorded stage transition yet (brand-new or "
              "not processed yet)",
        "id": "Kirim pesan pertama tapi belum ada perpindahan tahap tercatat (baru atau belum "
              "diproses)",
    },
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
    "chat.recognize":   {"ru": "Распознать", "en": "Recognize", "id": "Kenali"},
    "chat.recognize_again": {"ru": "Распознать заново", "en": "Recognize again",
                             "id": "Kenali ulang"},
    "chat.recognize_hint": {
        "ru": "Отправить в брокер: голос → текст, картинка → описание. Результат попадёт "
              "в чат, и Степан учтёт его в следующем ответе.",
        "en": "Send to the broker: voice → text, image → description. The result lands in "
              "the chat and Stepan uses it in its next reply.",
        "id": "Kirim ke broker: suara → teks, gambar → deskripsi. Hasilnya masuk ke chat "
              "dan dipakai Stepan di balasan berikutnya."},
    "chat.recognize_failed": {
        "ru": "Не удалось распознать — брокер недоступен или формат не поддержан",
        "en": "Recognition failed — the broker is unavailable or the format is unsupported",
        "id": "Gagal mengenali — broker tidak tersedia atau format tidak didukung"},
    "log.stage_change": {"ru": "Стадия: {from} → {to}", "en": "Stage: {from} → {to}",
                         "id": "Tahap: {from} → {to}"},
    "product.none":     {"ru": "— без продукта —", "en": "— no product —",
                         "id": "— tanpa produk —"},
    "chat.product":     {"ru": "Продукт изменён", "en": "Product changed",
                         "id": "Produk diubah"},
    "chat.manager_note_set": {"ru": "Заметка менеджера обновлена", "en": "Manager note updated",
                              "id": "Catatan manajer diperbarui"},
    "chat.manager_note_cleared": {"ru": "Заметка менеджера очищена", "en": "Manager note cleared",
                                  "id": "Catatan manajer dihapus"},
    "chat.stage_reason": {"ru": "Причина смены стадии (бот)",
                          "en": "Stage change reason (bot)",
                          "id": "Alasan perubahan tahap (bot)"},
    "chat.send_stepan": {
        "ru": "Отправить как Стёпан",
        "en": "Send as Stepan",
        "id": "Kirim sebagai Stepan",
    },
    "chat.suggest_ph":  {"ru": "Черновик ответа…", "en": "Draft reply…",     "id": "Draf balasan…"},
    "chat.discard":     {"ru": "✗ Отменить",        "en": "✗ Discard",        "id": "✗ Buang"},
    "nav.mcp":          {"ru": "MCP",         "en": "MCP",         "id": "MCP"},
    "help.mcp": {
        "ru": ("Управление доступом по MCP: токены для внешних клиентов (двигать воронку "
               "или только читать чаты) и исходящая связь Степана с CRM. Плюс скачивание "
               "документации по подключению."),
        "en": ("MCP access: tokens for external clients (move the funnel or read-only chat "
               "access) and Stepan's outgoing CRM link. Plus the connection docs download."),
        "id": ("Akses MCP: token untuk klien eksternal (gerakkan funnel atau baca chat saja) "
               "dan koneksi keluar Stepan ke CRM. Plus unduh dokumentasi koneksi."),
    },
    # leads panel
    "nav.leads":        {"ru": "Лиды",        "en": "Leads",       "id": "Lead"},
    "nav.personas":     {"ru": "Персоны",     "en": "Personas",    "id": "Persona"},
    "pl.intro": {
        "ru": "Библиотека готовых персон продавцов (характер, тон, манера). Выбери персону "
              "для филиала, добавь свои филиальные инструкции по секциям. Товары остаются твои.",
        "en": "A library of ready seller personas (character, tone, style). Pick one for your "
              "branch and add your own branch instructions per section. Your catalog stays yours.",
        "id": "Perpustakaan persona penjual siap pakai (karakter, nada, gaya). Pilih untuk "
              "cabang Anda dan tambahkan instruksi cabang per bagian. Katalog tetap milik Anda."},
    "pl.using":  {"ru": "Активная персона:", "en": "In use:", "id": "Dipakai:"},
    "pl.draft":  {"ru": "Пока используется своя черновая персона филиала (persona_core).",
                  "en": "Currently using your own draft persona (persona_core).",
                  "id": "Saat ini memakai persona draf cabang Anda (persona_core)."},
    "pl.by":     {"ru": "Автор:", "en": "By", "id": "Oleh"},
    "pl.contact": {"ru": "Связаться", "en": "Contact", "id": "Hubungi"},
    "pl.contact_h": {"ru": "Написать автору, чтобы он обновил персону.",
                     "en": "Message the author to request a persona update.",
                     "id": "Kirim pesan ke penulis untuk minta pembaruan persona."},
    "pl.fav":    {"ru": "В избранное", "en": "Favorite", "id": "Favorit"},
    "pl.fav_h":  {"ru": "Пометить персону как избранную для этого филиала.",
                  "en": "Mark this persona as a favorite for this branch.",
                  "id": "Tandai persona ini sebagai favorit cabang."},
    "pl.use":    {"ru": "Выбрать для филиала", "en": "Use for this branch",
                  "id": "Pakai untuk cabang"},
    "pl.use_h":  {"ru": "Сделать эту персону активной для филиала (в живой промпт пока НЕ "
                        "включается — это следующий этап).",
                  "en": "Make this persona active for the branch (not wired into the live "
                        "prompt yet, that's the next phase).",
                  "id": "Jadikan persona aktif untuk cabang (belum masuk prompt live)."},
    "pl.in_use": {"ru": "Активна", "en": "In use", "id": "Aktif"},
    "pl.open":   {"ru": "Открыть", "en": "Open", "id": "Buka"},
    "pl.branches": {"ru": "филиалов", "en": "branches", "id": "cabang"},
    "pl.stat_h": {"ru": "Сколько филиалов используют персону и сколько добавили в избранное. "
                        "Метрики продаж по персоне появятся позже.",
                  "en": "How many branches use this persona and favorited it. Per-persona sales "
                        "stats come later.",
                  "id": "Berapa cabang memakai dan memfavoritkan. Statistik penjualan menyusul."},
    "pl.stats_note": {"ru": "Показаны метрики использования. Эффективность продаж по персоне — "
                            "следующий этап.",
                      "en": "Adoption stats shown. Per-persona sales effectiveness is next.",
                      "id": "Statistik pemakaian. Efektivitas penjualan per persona menyusul."},
    "pl.empty":  {"ru": "В библиотеке пока нет персон.", "en": "No personas in the library yet.",
                  "id": "Belum ada persona di perpustakaan."},
    "pl.back":   {"ru": "← Библиотека", "en": "← Library", "id": "← Perpustakaan"},
    "pl.detail_intro": {
        "ru": "Ядро персоны только для чтения. Под каждой секцией добавь свои филиальные "
              "инструкции — они будут дополнять эту секцию для твоего филиала.",
        "en": "The persona core is read-only. Under each section add your own branch "
              "instructions that extend that section for your branch.",
        "id": "Inti persona hanya-baca. Di tiap bagian tambahkan instruksi cabang Anda."},
    "pl.add_label": {"ru": "Добавка филиала", "en": "Branch addendum", "id": "Tambahan cabang"},
    "pl.add_h":  {"ru": "Твои инструкции поверх этой секции — уникальное для филиала.",
                  "en": "Your instructions on top of this section, unique to the branch.",
                  "id": "Instruksi Anda untuk bagian ini, khusus cabang."},
    "pl.add_ph": {"ru": "напр. всегда упоминай нашу рассрочку 0%",
                  "en": "e.g. always mention our 0% instalment",
                  "id": "mis. selalu sebut cicilan 0% kami"},
    "pl.add_none": {"ru": "нет", "en": "none", "id": "tidak ada"},
    "pl.save":   {"ru": "Сохранить", "en": "Save", "id": "Simpan"},
    "pl.readonly_note": {
        "ru": "Обновлять само ядро персоны может только её автор. Нужны правки — свяжись с "
              "автором (кнопка выше).",
        "en": "Only the persona's author can update its core. Need a change? Contact the "
              "author (button above).",
        "id": "Hanya penulis yang bisa memperbarui inti persona. Hubungi penulis di atas."},
    "pl.import": {"ru": "Импортировать", "en": "Import", "id": "Impor"},
    "pl.changed_ph": {"ru": "что изменилось (для истории версий)",
                      "en": "what changed (for the version history)",
                      "id": "apa yang berubah (untuk riwayat versi)"},
    "pl.history": {"ru": "История версий", "en": "Version history", "id": "Riwayat versi"},
    "pl.import_h": {"ru": "Снять снимок текущей персоны филиала (ядро + плейбуки + reference + "
                          "sales, всё кроме продуктов) в библиотеку как новую версию.",
                    "en": "Snapshot this branch's current persona (core + playbooks + references "
                          "+ sales, everything except products) into the library as a new version.",
                    "id": "Simpan persona cabang ini (inti + playbook + referensi + sales, "
                          "semua kecuali produk) ke perpustakaan sebagai versi baru."},
    "pl.gone":   {"ru": "Персона не найдена.", "en": "Persona not found.",
                  "id": "Persona tidak ditemukan."},
    "pl.pick_branch": {"ru": "Выбери один филиал в фильтре, чтобы менять его персону.",
                       "en": "Pick a single branch in the filter to change its persona.",
                       "id": "Pilih satu cabang di filter untuk mengubah personanya."},
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
    "outbox.eta":       {"ru": "Уйдёт",        "en": "Send in",     "id": "Kirim dlm"},
    "outbox.now":       {"ru": "сейчас",       "en": "now",         "id": "sekarang"},
    "outbox.in_s":      {"ru": "через {n}с",   "en": "in {n}s",     "id": "{n}s lagi"},
    "outbox.in_m":      {"ru": "через {n} мин","en": "in {n} min",  "id": "{n} mnt lagi"},
    "outbox.in_h":      {"ru": "через {n} ч",  "en": "in {n}h",     "id": "{n} jam lagi"},
    "outbox.quiet_until": {"ru": "тихо до {h}:00", "en": "quiet till {h}:00",
                           "id": "senyap s/d {h}:00"},
    "outbox.cap_held": {"ru": "лимит {limit} исчерпан", "en": "{limit} cap reached",
                        "id": "batas {limit} tercapai"},
    "outbox.cap_hour": {"ru": "в час", "en": "hourly", "id": "per jam"},
    "outbox.cap_day":  {"ru": "в день", "en": "daily", "id": "per hari"},
    "outbox.sending_paused": {"ru": "отправка на паузе", "en": "sending paused",
                              "id": "pengiriman dijeda"},
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
    "log.h.id":   {"ru": "ID запроса к брокеру — для сверки с логом брокера/провайдера.",
                   "en": "Broker request id — to cross-check the broker/provider log.",
                   "id": "ID permintaan broker — untuk mencocokkan dengan log broker."},
    "log.h.when": {"ru": "Время вызова (в твоём часовом поясе).",
                   "en": "When the call happened (your time zone).",
                   "id": "Waktu panggilan (zona waktu Anda)."},
    "log.h.kind": {"ru": "Тип вызова: reply (ответ), embed (векторизация), verify (проверка), "
                         "translate, coach и т.п.",
                   "en": "Call kind: reply, embed, verify, translate, coach, etc.",
                   "id": "Jenis panggilan: reply, embed, verify, translate, coach, dll."},
    "log.h.chat": {"ru": "Чат/тред, к которому относится вызов — клик открывает диалог.",
                   "en": "The chat/thread the call belongs to — click opens the dialog.",
                   "id": "Chat/thread terkait panggilan — klik untuk buka dialog."},
    "log.h.cap":  {"ru": "Capability — класс модели: chat:fast, chat:smart, embed, translate.",
                   "en": "Capability — model class: chat:fast, chat:smart, embed, translate.",
                   "id": "Capability — kelas model: chat:fast, chat:smart, embed, translate."},
    "log.h.model": {"ru": "Конкретная модель, которую брокер подобрал под этот вызов.",
                    "en": "The exact model the broker picked for this call.",
                    "id": "Model persis yang dipilih broker untuk panggilan ini."},
    "log.h.tok":  {"ru": "Токенов потрачено (вход + выход).",
                   "en": "Tokens spent (input + output).",
                   "id": "Token terpakai (masukan + keluaran)."},
    "log.h.cost": {"ru": "Стоимость вызова в USD (free = бесплатный провайдер).",
                   "en": "Call cost in USD (free = a free provider).",
                   "id": "Biaya panggilan dalam USD (free = penyedia gratis)."},
    "log.h.dur":  {"ru": "Задержка — сколько брокер отвечал на этот вызов.",
                   "en": "Latency — how long the broker took on this call.",
                   "id": "Latensi — berapa lama broker menjawab panggilan ini."},
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
    "ch.active":   {"ru": "Канал включён",     "en": "Channel enabled",   "id": "Channel aktif"},
    "ch.active_hint": {
        "ru": "Выключи, если Instagram блокирует отправку (ошибка 403 / action block) — "
              "приём и отправка через этот канал остановятся, ничего не потеряется. "
              "Включи обратно, когда блок снимется (обычно несколько часов).",
        "en": "Turn off if Instagram is blocking sends (403 / action block) — this pauses "
              "both receiving and sending on this channel, nothing is lost. Turn back on "
              "once the block clears (usually a few hours).",
        "id": "Matikan jika Instagram memblokir pengiriman (403 / action block) — channel "
              "ini berhenti menerima & mengirim, tidak ada yang hilang. Nyalakan lagi "
              "setelah blokir hilang (biasanya beberapa jam).",
    },
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
    "ch.step1": {"ru": "Шаг 1 из 2 · Вход", "en": "Step 1 of 2 · Login",
                "id": "Langkah 1 dari 2 · Login"},
    "ch.step2": {"ru": "Шаг 2 из 2 · Подтверждение", "en": "Step 2 of 2 · Verify",
                "id": "Langkah 2 dari 2 · Verifikasi"},
    "ch.for_account": {"ru": "Аккаунт:", "en": "Account:", "id": "Akun:"},
    "ch.hint_login": {
        "ru": "После входа Instagram может попросить код подтверждения — это нормально, "
              "появится следующий шаг с объяснением, какой именно код нужен.",
        "en": "After you submit this, Instagram may ask for a verification code — that's "
              "normal, the next step will explain exactly which code it needs.",
        "id": "Setelah ini, Instagram mungkin minta kode verifikasi — itu normal, langkah "
              "berikutnya akan menjelaskan kode mana yang dibutuhkan.",
    },
    "ch.code_challenge": {"ru": "Код подтверждения", "en": "Verification code",
                          "id": "Kode verifikasi"},
    "ch.hint_2fa": {
        "ru": "Двухфакторная аутентификация включена на этом аккаунте. Введите код из "
              "приложения-аутентификатора (Google Authenticator и т.п.) или из SMS.",
        "en": "Two-factor authentication is on for this account. Enter the code from your "
              "authenticator app (Google Authenticator, etc.) or SMS.",
        "id": "Autentikasi dua faktor aktif di akun ini. Masukkan kode dari aplikasi "
              "authenticator (Google Authenticator, dll.) atau SMS.",
    },
    "ch.hint_challenge": {
        "ru": "Это НЕ код двухфакторной аутентификации. Instagram посчитал этот вход "
              "подозрительным (новое устройство/сервер) и отправил код подтверждения на "
              "email или телефон, привязанные к аккаунту — проверьте почту/SMS.",
        "en": "This is NOT a two-factor code. Instagram flagged this login as unusual (new "
              "device/server) and sent a verification code to the email or phone linked to "
              "the account — check your inbox/SMS.",
        "id": "Ini BUKAN kode dua faktor. Instagram menganggap login ini mencurigakan "
              "(perangkat/server baru) dan mengirim kode verifikasi ke email atau nomor "
              "telepon yang terhubung ke akun — cek email/SMS Anda.",
    },
    "ch.start_over": {"ru": "Начать заново", "en": "Start over", "id": "Mulai lagi"},
    "ch.hint_manual": {
        "ru": "Код здесь не поможет — Instagram требует подтверждения прямо в приложении. "
              "Откройте официальный Instagram (приложение или сайт) на доверенном "
              "устройстве и подтвердите вход там. Больше ничего нажимать не нужно — "
              "мы сами дожмём вход, как только вы подтвердите.",
        "en": "No code will help here — Instagram requires confirming this login inside the "
              "app itself. Open the official Instagram app or website on a trusted device and "
              "approve the login there. Nothing else to click — we finish the login for you "
              "as soon as you approve.",
        "id": "Kode tidak akan membantu di sini — Instagram meminta konfirmasi langsung di "
              "aplikasi. Buka aplikasi atau situs Instagram resmi di perangkat tepercaya dan "
              "setujui login di sana. Tidak perlu klik apa pun lagi — kami menyelesaikan "
              "login begitu Anda menyetujui.",
    },
    "ch.waiting_approve": {
        "ru": "Ждём подтверждения в приложении Instagram — войдём автоматически",
        "en": "Waiting for your approval in the Instagram app — we'll log in automatically",
        "id": "Menunggu persetujuan di aplikasi Instagram — kami login otomatis"},
    "ch.poll_gave_up": {
        "ru": "Подтверждения пока не видно. Мы перестали проверять автоматически, чтобы не "
              "долбить Instagram повторными входами (это ведёт к блокировке). Подтвердите в "
              "приложении и нажмите кнопку ниже.",
        "en": "No approval seen yet. We stopped checking automatically so we don't hammer "
              "Instagram with repeated logins (that gets accounts blocked). Approve in the "
              "app, then use the button below.",
        "id": "Persetujuan belum terlihat. Kami berhenti memeriksa otomatis agar tidak "
              "membanjiri Instagram dengan login berulang (itu memicu blokir). Setujui di "
              "aplikasi, lalu gunakan tombol di bawah."},
    "ch.flow_expired": {
        "ru": "Начатый вход потерян (сервер перезапускался — обычно это деплой). "
              "Введите логин и пароль заново.",
        "en": "The login in progress was lost (the server restarted — usually a deploy). "
              "Please enter the username and password again.",
        "id": "Proses login yang berjalan hilang (server dimulai ulang — biasanya deploy). "
              "Silakan masukkan nama pengguna dan kata sandi lagi."},
    "ch.retry_manual": {"ru": "Я подтвердил — повторить", "en": "I've confirmed — retry",
                        "id": "Sudah dikonfirmasi — coba lagi"},
    "ch.hint_device": {
        "ru": "Instagram отправил запрос подтверждения входа на ваш телефон. Откройте "
              "уведомление Instagram и нажмите «Это я» / «Подтвердить» — и всё. Код вводить "
              "не нужно, кнопку жать тоже: мы сами дожмём вход, как только вы подтвердите.",
        "en": "Instagram sent a login-approval request to your phone. Open the Instagram "
              "notification and tap “It's me” / “Approve” — that's all. No code to type and "
              "no button to press: we finish the login as soon as you approve.",
        "id": "Instagram mengirim permintaan persetujuan login ke ponsel Anda. Buka notifikasi "
              "Instagram dan ketuk “Ini saya” / “Setujui” — selesai. Tanpa kode dan tanpa "
              "tombol: kami menyelesaikan login begitu Anda menyetujui.",
    },
    "ch.continue_device": {"ru": "Я подтвердил на телефоне — продолжить",
                           "en": "I approved on my phone — continue",
                           "id": "Sudah disetujui di ponsel — lanjutkan"},
    "ch.already_confirmed": {
        "ru": "Уже подтвердил в приложении — повторить",
        "en": "Already confirmed in the app — retry",
        "id": "Sudah dikonfirmasi di aplikasi — coba lagi",
    },
    "ch.advanced_json": {
        "ru": "Продвинутый вариант: вставить готовую сессию (Session JSON)",
        "en": "Advanced: paste an existing session (Session JSON)",
        "id": "Lanjutan: tempel sesi yang sudah ada (Session JSON)",
    },
    "ch.hint_json": {
        "ru": "Только если у вас уже есть экспортированная сессия instagrapi с другого "
              "входа — заменяет логин/пароль и код подтверждения полностью.",
        "en": "Only if you already have an exported instagrapi session from elsewhere — "
              "replaces login/password and the verification code entirely.",
        "id": "Hanya jika Anda sudah punya sesi instagrapi yang diekspor dari tempat lain — "
              "menggantikan login/kata sandi dan kode verifikasi sepenuhnya.",
    },
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
    "br.name_h": {
        "ru": "Название филиала — как он показан в списке, фильтрах и отчётах.",
        "en": "Branch name — as shown in the list, filters and reports.",
        "id": "Nama cabang — seperti tampil di daftar, filter, dan laporan."},
    "br.lang_h": {
        "ru": "Язык, на котором бот по умолчанию отвечает лидам этого филиала.",
        "en": "Default language the bot replies in for this branch's leads.",
        "id": "Bahasa default bot untuk membalas lead cabang ini."},
    "br.tz_h": {
        "ru": "Часовой пояс филиала — для аналитики «пиковые часы» и тихих часов отправки. "
              "На отображение времени в твоём интерфейсе не влияет (оно в твоём поясе).",
        "en": "Branch time zone — for peak-hour analytics and the quiet-hours send window. "
              "Does not affect how times are shown to you (those follow your own zone).",
        "id": "Zona waktu cabang — untuk analitik jam sibuk dan jendela jam tenang pengiriman. "
              "Tidak memengaruhi tampilan waktu untuk Anda."},
    "br.active_h": {
        "ru": "Выключенный филиал не обрабатывается ботом и скрыт из рабочих списков.",
        "en": "An inactive branch isn't processed by the bot and is hidden from working lists.",
        "id": "Cabang nonaktif tidak diproses bot dan disembunyikan dari daftar kerja."},
    "br.kb_source_h": {
        "ru": "Брать базу знаний из другого филиала (здесь она read-only). Пусто = своя база.",
        "en": "Use another branch's knowledge base (read-only here). Empty = its own base.",
        "id": "Gunakan basis pengetahuan cabang lain (read-only di sini). Kosong = milik sendiri."},
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
    "bot.sending":   {"ru": "Отправка (исходящие)", "en": "Sending (outbound)",
                      "id": "Pengiriman (keluar)"},
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
    "rep.closed_period": {"ru": "Закрыто за период", "en": "Closed in period",
                          "id": "Ditutup periode ini"},
    "rep.conv":    {"ru": "Конверсия",      "en": "Conversion",   "id": "Konversi"},
    "rep.dormant": {"ru": "Спящие",         "en": "Dormant",      "id": "Tidak aktif"},
    # Counts a real captured PAIN, not a pass through the 'qualifying' stage (every lead
    # crosses that, so the old wording read ~87% while only ~65% had any pain at all).
    "rep.discovered": {"ru": "С болью до презент.", "en": "Pain captured",
                       "id": "Pain tergali"},
    "rep.disc_len": {"ru": "Ср. глубина выявл.", "en": "Avg discovery msgs",
                     "id": "Rata2 gali"},
    "rep.msgs_tile": {"ru": "Сообщений исх/вх", "en": "Msgs out/in",
                      "id": "Pesan keluar/masuk"},
    "rep.funnel":  {"ru": "Воронка продаж",  "en": "Sales funnel", "id": "Corong penjualan"},
    "flow.entry":  {"ru": "вход",            "en": "entry",        "id": "masuk"},
    "rep.from":    {"ru": "С даты",          "en": "From",         "id": "Dari"},
    "rep.to":      {"ru": "По дату",         "en": "To",           "id": "Sampai"},
    "rep.apply":   {"ru": "Показать",        "en": "Apply",        "id": "Terapkan"},
    "rep.date_hint": {"ru": "Дата = старт диалога с лидом",
                      "en": "Date = when the lead conversation started",
                      "id": "Tanggal = mulai percakapan lead"},
    "rep.range_1h":  {"ru": "1 час",   "en": "1 hour",   "id": "1 jam"},
    "rep.range_2h":  {"ru": "2 часа",  "en": "2 hours",  "id": "2 jam"},
    "rep.range_4h":  {"ru": "4 часа",  "en": "4 hours",  "id": "4 jam"},
    "rep.range_8h":  {"ru": "8 часов", "en": "8 hours",  "id": "8 jam"},
    "rep.range_12h": {"ru": "12 часов","en": "12 hours", "id": "12 jam"},
    "rep.range_24h": {"ru": "24 часа", "en": "24 hours",  "id": "24 jam"},
    "rep.range_7d":  {"ru": "7 дней",  "en": "7 days",    "id": "7 hari"},
    "rep.range_30d": {"ru": "30 дней", "en": "30 days",   "id": "30 hari"},
    "rep.range_60d": {"ru": "60 дней", "en": "60 days",   "id": "60 hari"},
    "rep.range_90d": {"ru": "90 дней", "en": "90 days",   "id": "90 hari"},
    "rep.range_all": {"ru": "Весь период", "en": "Full period", "id": "Semua periode"},
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
    "rep.by_hour": {"ru": "по часам суток (0-23, WIB)", "en": "by hour of day (0-23, WIB)",
                    "id": "per jam (0-23, WIB)"},
    "rep.peak": {"ru": "пик {n} сообщ. в {h}:00", "en": "peak {n} msgs at {h}:00",
                 "id": "puncak {n} pesan jam {h}:00"},
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

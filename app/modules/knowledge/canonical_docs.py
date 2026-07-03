"""Canonical KB doc/section data — the default skeleton every branch gets.

Split out of canonical.py to stay under the ~200 line ceiling; canonical.py holds the
logic (ensure_canonical_docs), this module holds the data it applies."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.settings.schema import I18n as _L


@dataclass(frozen=True)
class Section:
    key: str
    title: _L
    hint: _L


@dataclass(frozen=True)
class CanonDoc:
    slug: str
    category: str
    order: int
    title: _L
    sections: list[Section] = field(default_factory=list)


# category → localized group label (tree headers in the KB sidebar)
CATEGORIES: dict[str, _L] = {
    "persona": {"ru": "Персона", "en": "Persona", "id": "Persona"},
    "playbook": {"ru": "Плейбуки", "en": "Playbooks", "id": "Playbook"},
    "reference": {"ru": "Справочник", "en": "Reference", "id": "Referensi"},
}


def _s(key: str, ru_t: str, en_t: str, id_t: str, ru_h: str, en_h: str, id_h: str) -> Section:
    return Section(key, {"ru": ru_t, "en": en_t, "id": id_t},
                   {"ru": ru_h, "en": en_h, "id": id_h})


def _d(slug: str, cat: str, order: int, ru: str, en: str, id_: str,
       sections: list[Section]) -> CanonDoc:
    return CanonDoc(slug, cat, order, {"ru": ru, "en": en, "id": id_}, sections)


CANONICAL_DOCS: list[CanonDoc] = [
    _d("persona_core", "persona", 0,
       "Персона — ядро", "Persona — core", "Persona — inti", [
        _s("identity", "Личность", "Identity", "Identitas",
           "Кто такой ассистент: имя, роль, от чьего лица пишет, тон.",
           "Who the assistant is: name, role, whose voice, tone.",
           "Siapa asisten: nama, peran, atas nama siapa, nada."),
        _s("voice", "Голос и стиль", "Voice & style", "Gaya bahasa",
           "Как звучит: длина сообщений, эмодзи, пунктуация, что нельзя.",
           "How it sounds: message length, emoji, punctuation, don'ts.",
           "Cara bicara: panjang pesan, emoji, tanda baca, larangan."),
        _s("rules", "Жёсткие правила", "Hard rules", "Aturan keras",
           "Чего НИКОГДА не делать: не выдумывать цены/даты, не спамить.",
           "What to NEVER do: never invent prices/dates, never spam.",
           "Yang TIDAK boleh: karang harga/tanggal, spam."),
        _s("formula", "Формула сообщения", "Message formula", "Formula pesan",
           "Как строить ответ: реакция → ценность → следующий шаг/вопрос.",
           "How to build a reply: react → value → next step/question.",
           "Cara menyusun balasan: reaksi → nilai → langkah/pertanyaan."),
    ]),
    _d("faq", "reference", 0, "FAQ", "FAQ", "FAQ", [
        _s("general", "Общие вопросы", "General", "Umum",
           "Частые вопросы и короткие точные ответы.",
           "Common questions and short precise answers.",
           "Pertanyaan umum dan jawaban singkat."),
        _s("payment", "Оплата", "Payment", "Pembayaran",
           "Способы оплаты, рассрочка, возвраты — только факты.",
           "Payment methods, installments, refunds — facts only.",
           "Metode bayar, cicilan, refund — fakta saja."),
        _s("logistics", "Логистика", "Logistics", "Logistik",
           "Адрес, часы работы, формат (онлайн/офлайн), контакты.",
           "Address, hours, format (online/offline), contacts.",
           "Alamat, jam, format (online/offline), kontak."),
    ]),
    _d("market_facts", "reference", 1,
       "Факты рынка / ROI", "Market facts / ROI", "Fakta pasar / ROI", [
        _s("roi", "ROI и зарплаты", "ROI & salaries", "ROI & gaji",
           "Цифры-якоря: зарплаты, ставки, окупаемость обучения.",
           "Anchor numbers: salaries, rates, payback of the course.",
           "Angka jangkar: gaji, tarif, balik modal kursus."),
    ]),
    _d("stories", "reference", 2,
       "Истории / соцдоказательство", "Stories / social proof", "Cerita / bukti sosial", [
        _s("testimonials", "Отзывы и кейсы", "Testimonials & cases", "Testimoni & kasus",
           "Реальные истории выпускников — короткие, конкретные.",
           "Real graduate stories — short and concrete.",
           "Kisah alumni nyata — singkat dan konkret."),
    ]),
    _d("market_competitors", "reference", 3,
       "Конкуренты / сравнение", "Competitors / comparison", "Kompetitor / perbandingan", [
        _s("landscape", "Обзор конкурентов", "Competitor landscape", "Lanskap kompetitor",
           "Кто ещё на рынке, цены/формат, честные плюсы и минусы каждого.",
           "Who else is out there, price/format, honest pros and cons of each.",
           "Siapa lagi di pasar, harga/format, plus-minus jujur."),
        _s("edge", "Наше отличие (честно)", "Our honest edge", "Keunggulan kita (jujur)",
           "Чем мы честно отличаемся под конкретную потребность — без вранья.",
           "How we honestly differ for a given need — no lies.",
           "Bagaimana kita berbeda jujur untuk kebutuhan tertentu."),
    ]),
    _d("playbook_discovery", "playbook", 0,
       "Выявление потребности (SPIN)", "Needs discovery (SPIN)", "Gali kebutuhan (SPIN)", [
        _s("situation", "Ситуация", "Situation", "Situasi",
           "Вопросы про контекст и цель лида (задачу): чем занят, чего хочет достичь.",
           "Questions on the lead's context and goal (their job to be done).",
           "Pertanyaan konteks & tujuan lead (job)."),
        _s("problem", "Проблема (боль)", "Problem (pain)", "Masalah (pain)",
           "Вопросы, вскрывающие трудность/страх/препятствие лида.",
           "Questions that surface the lead's difficulty / fear / obstacle.",
           "Pertanyaan yang menggali kesulitan / rasa takut lead."),
        _s("implication", "Усиление (цена бездействия)", "Implication (cost of inaction)",
           "Implikasi",
           "Вопросы, показывающие, во что обходится не решать боль. Здесь больше всего усилий.",
           "Questions on what it costs to leave the pain unsolved. Spend the most effort here.",
           "Pertanyaan biaya membiarkan masalah. Fokus paling banyak di sini."),
        _s("need_payoff", "Выгода (need-payoff)", "Need-payoff (gain)", "Manfaat (gain)",
           "Вопросы, дающие лиду самому проговорить желаемый результат (выгоду).",
           "Questions that let the lead voice the outcome they want (the gain).",
           "Pertanyaan agar lead menyuarakan hasil yang diinginkan (gain)."),
    ]),
    _d("sales_mastery", "playbook", 0,
       "Мастерство продаж", "Sales mastery", "Penguasaan sales", [
        _s("techniques", "Техники (Challenger/Sandler/Cialdini/Voss)",
           "Techniques (Challenger/Sandler/Cialdini/Voss)", "Teknik",
           "Продвинутые техники: как думать и вести как мастер-консультант, не как продавец.",
           "Advanced techniques: think and lead like a master advisor, not a salesman.",
           "Teknik lanjutan: berpikir seperti penasihat, bukan penjual."),
        _s("readiness", "Сигналы готовности к покупке", "Buying-readiness signals",
           "Sinyal siap beli",
           "Как понять, что лид психологически готов к презентации/цене.",
           "How to tell the lead is psychologically ready for the pitch/price.",
           "Cara tahu lead siap secara psikologis."),
        _s("doubts", "Снятие сомнений (без вранья)", "Doubt removal (no lies)",
           "Hilangkan keraguan",
           "Как убрать сомнения честно: соцдоказательство, снятие риска, топ-5 страхов.",
           "Remove doubts honestly: social proof, risk-reversal, the top-5 fears.",
           "Hilangkan keraguan jujur: bukti sosial, top-5 ketakutan."),
    ]),
    _d("playbook_qualify", "playbook", 1,
       "Квалификация", "Qualification", "Kualifikasi", [
        _s("qualify", "Как квалифицировать", "How to qualify", "Cara kualifikasi",
           "Вопросы для выявления уровня и цели лида до продажи.",
           "Questions to read the lead's level and goal before selling.",
           "Pertanyaan membaca level & tujuan lead sebelum jualan."),
        _s("routing", "Подбор продукта", "Product routing", "Routing produk",
           "Как по ответам выбрать подходящий продукт из каталога.",
           "How answers map to the right product from the catalog.",
           "Cara jawaban memetakan produk yang tepat."),
    ]),
    _d("playbook_price", "playbook", 1,
       "Цена и возражения", "Price & objections", "Harga & keberatan", [
        _s("value", "Подача ценности", "Value framing", "Framing nilai",
           "Как показать ценность ДО цены. Реформулировки «дорого».",
           "Show value BEFORE the number. Reframes for 'expensive'.",
           "Tunjukkan nilai SEBELUM angka. Reframe 'mahal'."),
        _s("objections", "Возражения", "Objections", "Keberatan",
           "Ответы на «дорого/долго/подумаю» — признать→реформулировать→вопрос.",
           "Handling 'pricey/long/I'll think' — ack→reframe→question.",
           "Tangani 'mahal/lama/pikir dulu' — akui→reframe→tanya."),
        _s("payment", "Рычаги оплаты", "Payment levers", "Opsi pembayaran",
           "Рассрочка, скидки, ранняя бронь — что и когда предлагать.",
           "Installments, discounts, early-bird — what to offer when.",
           "Cicilan, diskon, early-bird — tawarkan apa & kapan."),
    ]),
    _d("playbook_close", "playbook", 2, "Закрытие", "Closing", "Closing", [
        _s("close", "Как закрывать", "How to close", "Cara closing",
           "Подвести к шагу записи; не дать лиду «остыть».",
           "Move to the booking step; keep the lead from going cold.",
           "Arahkan ke langkah daftar; jaga lead tetap hangat."),
        _s("dormant", "Профилактика ухода", "Dormant prevention", "Cegah dormant",
           "Что делать с «подумаю»/тишиной, как мягко вернуть.",
           "Handling 'I'll think'/silence, how to warmly re-engage.",
           "Tangani 'pikir dulu'/diam, cara re-engage."),
    ]),
    _d("playbook_ready", "playbook", 3,
       "Готовность / запись", "Ready / registration", "Siap / pendaftaran", [
        _s("ready_flow", "Поток записи", "Registration flow", "Alur pendaftaran",
           "Шаги от согласия до записи; какие данные собрать.",
           "Steps from yes to enrolled; what data to collect.",
           "Langkah dari setuju ke terdaftar; data apa diambil."),
        _s("contact", "Сбор контакта", "Contact capture", "Ambil kontak",
           "Готов ТОЛЬКО когда есть имя + телефон/WA. Как их запросить.",
           "Ready ONLY with name + phone/WA. How to ask for them.",
           "Siap HANYA jika ada nama + telp/WA. Cara memintanya."),
    ]),
    _d("playbook_meetings", "playbook", 4,
       "Встречи и демо", "Meetings & demos", "Pertemuan & demo", [
        _s("demos", "Демо и звонки", "Demos & calls", "Demo & panggilan",
           "Как предложить демо/звонок/визит в офис и договориться.",
           "How to offer a demo/call/office visit and set it up.",
           "Cara tawarkan demo/panggilan/kunjungan & atur jadwal."),
    ]),
    _d("playbook_format", "playbook", 5,
       "Формат сообщений", "Message format", "Format pesan", [
        _s("formatting", "Правила оформления", "Formatting rules", "Aturan format",
           "Разбивка на бабблы (|||), списки с новой строки, длина.",
           "Bubble split (|||), list items on own lines, length.",
           "Pisah bubble (|||), item daftar per baris, panjang."),
    ]),
    _d("playbook_social", "playbook", 6,
       "Общение и юмор", "Small-talk & humor", "Obrolan & humor", [
        _s("social", "Смолл-ток и негатив", "Small-talk & negativity", "Obrolan & negatif",
           "Как реагировать на шутки, оффтоп, грубость — по-человечески.",
           "Reacting to jokes, off-topic, rudeness — like a human.",
           "Menanggapi lelucon, off-topic, kasar — manusiawi."),
    ]),
]

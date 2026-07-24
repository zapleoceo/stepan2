"""Canonical KB doc/section data — the default skeleton every branch gets.

Exactly the docs the reply prompt actually loads (knowledge.service: _PERSONA_ORDER +
_ALWAYS_DOC_SLUGS + OBJECTION_PLAYBOOK_SLUG) — nothing else. The previous 14-doc skeleton
(faq/stories/9 playbooks/…) created editors for text the model never saw; the sales method
now lives with the model, and the KB holds only the facts it grounds replies in.

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


# category → localized group label (tree headers in the KB sidebar). "reference" is kept
# only so pre-cleanup docs from the old skeleton still render under a labelled group.
CATEGORIES: dict[str, _L] = {
    "persona": {"ru": "Персона", "en": "Persona", "id": "Persona"},
    "facts": {"ru": "Факты", "en": "Facts", "id": "Fakta"},
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
    ]),
    _d("facts_policy", "facts", 0,
       "Факты — оплата и правила", "Facts — payment & policy", "Fakta — bayar & aturan", [
        _s("payment", "Оплата и рассрочка", "Payment & installments", "Bayar & cicilan",
           "Способы оплаты, DP, рассрочка, реквизиты — только то, что школа исполнит.",
           "Payment methods, DP, installments, requisites — only what the school honours.",
           "Metode bayar, DP, cicilan — hanya yang pasti berlaku."),
        _s("discounts", "Скидки и акции", "Discounts & promos", "Diskon & promo",
           "Действующие скидки/дедлайны. Нет в этом файле — значит не существует.",
           "Live discounts/deadlines. Not written here — doesn't exist.",
           "Diskon/tenggat aktif. Tidak tertulis = tidak ada."),
        _s("rules", "Правила и запреты", "Rules & prohibitions", "Aturan & larangan",
           "Сертификаты, возвраты, возрастные правила, NEVER-список.",
           "Certificates, refunds, age rules, the NEVER-list.",
           "Sertifikat, refund, aturan usia, daftar NEVER."),
    ]),
    _d("facts_market", "facts", 1,
       "Факты — школа и рынок", "Facts — school & market", "Fakta — sekolah & pasar", [
        _s("institution", "Школа", "The school", "Sekolah",
           "Факты об академии: история, адрес, формат, платформа.",
           "Facts about the academy: history, address, format, platform.",
           "Fakta akademi: sejarah, alamat, format, platform."),
        _s("market", "Рынок и доход", "Market & income", "Pasar & penghasilan",
           "Зарплатные вилки, конкуренты, успешные кейсы — с источником.",
           "Salary ranges, competitors, success cases — sourced.",
           "Kisaran gaji, kompetitor, kasus sukses — bersumber."),
    ]),
    _d("objection_playbook", "playbook", 0,
       "Банк аргументов (возражения)", "Objection argument bank", "Bank argumen keberatan", [
        _s("price", "Цена", "Price", "Harga",
           "Аргументы на «дорого» — секция ## price.",
           "Arguments for 'expensive' — section ## price.",
           "Argumen untuk 'mahal' — bagian ## price."),
        _s("time", "Время", "Time", "Waktu",
           "«Нет времени / долго» — секция ## time.",
           "'No time / too long' — section ## time.",
           "'Tidak ada waktu' — bagian ## time."),
        _s("trust", "Доверие", "Trust", "Kepercayaan",
           "«Почему вы» — секция ## trust.",
           "'Why you' — section ## trust.",
           "'Kenapa kalian' — bagian ## trust."),
        _s("job_outcome", "Результат/работа", "Job outcome", "Hasil kerja",
           "«А работу найду?» — секция ## job_outcome.",
           "'Will I get a job?' — section ## job_outcome.",
           "'Dapat kerja?' — bagian ## job_outcome."),
    ]),
]

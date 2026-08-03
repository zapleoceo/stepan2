"""The handful of lines the CODE writes to a lead, in the language of the conversation.

Everything else a lead reads is written by the model, which is told to answer in their
language and does. These are the exceptions — a hand-off closing, the hold-line the money
gate ships instead of a bad draft, the clarifier that answers a bare "hi", the public comment
fallback — and until 2026-08-03 every one of them was a hardcoded Indonesian constant sitting
next to the code that sent it. A branch in any other language shipped Bahasa to its customers,
mid-conversation, with no way to notice: they are appended AFTER the model's reply, so the
reply itself reads fine.

One table rather than nine constants, because the failure was never one string — it was that
each lived alone and nobody looked at them together. Indonesian is the default for `id` and
byte-identical to what branch 1 sends today; an unknown language falls back to ENGLISH, never
to Indonesian. A language we have no translation for reading English is a small failure; one
reading Bahasa Indonesia is a conversation ending.

Working hours are Jakarta's and are stated here as such — they belong in branch data, and
there is no field for them yet. Named so the next person can find every copy at once.
"""
from __future__ import annotations

_FALLBACK = "en"

_CANNED: dict[str, dict[str, str]] = {
    # Sent instead of the offending draft whenever the money gate escalates — thread 5019
    # showed that flagging needs_human without replacing `.reply` protected the CRM record but
    # still shipped the bad draft to the lead. Content-free and consistent in tone with the
    # manager closing, so the lead doesn't get two conflicting "our team will help" lines.
    "escalation_hold": {
        "id": "Kakak, bentar ya - aku cek dulu ke tim supaya infonya pas dan akurat. "
              "Nanti dibantu langsung di jam kerja (Senin-Jumat, 09.00-18.00 WIB) 🙏",
        "en": "One moment please - let me check with the team so the details I give you are "
              "exact. Someone will get back to you during working hours "
              "(Mon-Fri, 09:00-18:00) 🙏",
        "ru": "Секунду, я уточню у команды, чтобы информация была точной. Вам ответят "
              "в рабочее время (Пн-Пт, 09:00-18:00) 🙏",
    },
    # A needs_manager turn always mutes the bot (agent_enabled=False) — but the model's own
    # reply that same turn is about answering the lead's question, not about announcing a
    # hand-off; nothing told the LEAD a human is now taking over. A lead who then sends a
    # follow-up (e.g. a phone number, thread 1023) gets pure silence — no bot, no confirmation,
    # because the account is already muted.
    "manager_closing": {
        "id": "Terima kasih ya Kak! Untuk ini tim kami yang akan bantu langsung - nanti "
              "dihubungi via telepon atau WhatsApp di jam kerja "
              "(Senin-Jumat, 09.00-18.00 WIB) ya 🙏",
        "en": "Thank you! Our team will take it from here - they'll reach you by phone or "
              "WhatsApp during working hours (Mon-Fri, 09:00-18:00) 🙏",
        "ru": "Спасибо! Дальше подключится наша команда - с вами свяжутся по телефону или "
              "в WhatsApp в рабочее время (Пн-Пт, 09:00-18:00) 🙏",
    },
    # Same closing, but the lead has NO phone on file yet (thread 452, 5005: needs_manager has
    # no phone gate the way the ready/READY path does — a real "ready to commit, no contact"
    # lead got muted and handed off with nothing for a manager to call). Asking here is the
    # only remaining chance: the bot is muted the instant this ships, but ingest's own phone
    # miner (identity._backfill) runs on EVERY inbound regardless of mute state, so a lead who
    # replies to THIS explicit ask still gets captured automatically.
    "manager_closing_no_phone": {
        "id": "Terima kasih ya Kak! Untuk ini tim kami yang akan bantu langsung. Boleh minta "
              "nomor telepon/WhatsApp Kakak? Biar tim bisa hubungi di jam kerja "
              "(Senin-Jumat, 09.00-18.00 WIB) ya 🙏",
        "en": "Thank you! Our team will take it from here. Could you share your phone or "
              "WhatsApp number, so they can reach you during working hours "
              "(Mon-Fri, 09:00-18:00)? 🙏",
        "ru": "Спасибо! Дальше подключится наша команда. Подскажете номер телефона или "
              "WhatsApp, чтобы с вами связались в рабочее время (Пн-Пт, 09:00-18:00)? 🙏",
    },
    # The sale/READY exit mutes the bot like MANAGER but never guaranteed the lead a closing —
    # it relied on the model's own reply confirming next steps, which isn't guaranteed.
    "ready_closing": {
        "id": "Siap Kak! Pendaftaran Kakak aku teruskan ke tim ya - nanti dihubungi via "
              "telepon atau WhatsApp di jam kerja (Senin-Jumat, 09.00-18.00 WIB) untuk "
              "langkah pembayaran & jadwal 🙏",
        "en": "Great! I'm passing your enrolment to the team - they'll reach you by phone or "
              "WhatsApp during working hours (Mon-Fri, 09:00-18:00) about the payment steps "
              "and the schedule 🙏",
        "ru": "Отлично! Передаю вашу заявку команде - с вами свяжутся по телефону или в "
              "WhatsApp в рабочее время (Пн-Пт, 09:00-18:00) по оплате и расписанию 🙏",
    },
    # Public comment fallback: the draft could not be grounded, so no facts — just a warm pull
    # into DMs. Posted under our own post, where a wrong-language reply is visible to everyone.
    "comment_fallback": {
        "id": "Halo Kak! 😊 Boleh DM aku ya biar aku bantu jelasin lengkap 🙏",
        "en": "Hi! 😊 Send me a DM and I'll walk you through the details 🙏",
        "ru": "Привет! 😊 Напишите мне в личные сообщения - расскажу всё подробно 🙏",
    },
    # The one reply the model does not write: an emoji or a garbled string gives it nothing
    # to react to, so a fixed clarifier is as good as a written one and costs nothing. It
    # names nobody — a branded line lived in opener.py until 2026-08-02 and introduced EVERY
    # branch as this product's first client. It stayed Indonesian, though, which is what a
    # reviewer typing "hi" at the demo branch reads: a bare hello is an ack, so it lands here
    # rather than with the model. A branch that wants its own greeting sets junk_opener.
    "junk_opener": {
        "id": "Halo Kak 😊 Boleh cerita, Kakak lagi cari info tentang apa ya?",
        "en": "Hi there 😊 What are you looking for - happy to help?",
        "ru": "Привет 😊 Расскажите, что вас интересует?",
    },
    # Who the comment prompt says it is. Not a line anyone reads — it sits in an
    # English-language prompt, so there is one non-Indonesian variant and no translations. A
    # name is tenant identity rather than language, but the Indonesian branches' persona is
    # called MinStep and their prompt must not change, so the name is the `id` default and
    # everybody else gets a description instead of another company's mascot. A branch that
    # wants its own name puts it in persona_core, which is in this same prompt's knowledge base.
    "comment_persona": {
        "id": "MinStep",
        "en": "the school's assistant on Instagram",
    },
}


def _text(key: str, lang: str | None) -> str:
    variants = _CANNED[key]
    return variants.get((lang or "").lower()) or variants[_FALLBACK]


def escalation_hold(lang: str | None) -> str:
    """The safe hold-line the money gate ships in place of an ungrounded draft."""
    return _text("escalation_hold", lang)


def manager_closing(lang: str | None, *, has_phone: bool) -> str:
    """Told to the lead the turn a human takes the conversation over."""
    key = "manager_closing" if has_phone else "manager_closing_no_phone"
    return _text(key, lang)


def ready_closing(lang: str | None) -> str:
    """Told to the lead the turn their enrolment goes to the team."""
    return _text("ready_closing", lang)


def comment_fallback(lang: str | None) -> str:
    """Posted publicly when nothing in the draft could be grounded in the knowledge base."""
    return _text("comment_fallback", lang)


def comment_persona(lang: str | None) -> str:
    """How the public-comment prompt introduces the writer to itself."""
    return _text("comment_persona", lang)


def junk_opener(settings_value: str | None, lang: str | None) -> str:
    """The branch's own greeting, or the unbranded fallback in the branch's own language.

    `lang` is required, unlike every other entry point here. It defaulted to Indonesian, which
    is the one thing this module says never to do — and this is the line most likely to be
    called by someone who has not thought about language at all, because it is a template and
    templates feel language-free. Passing None is fine and means English."""
    return (settings_value or "").strip() or _text("junk_opener", lang)

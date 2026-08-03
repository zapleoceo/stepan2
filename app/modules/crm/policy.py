"""Что Степану делать дальше, исходя из результата, который менеджер поставил в CRM.

Таблица, а не разветвлённый код: политика меняется решением владельца, а не рефакторингом.
Пять значений — это не весь каталог CRM (там их десять), а ровно те, которыми филиал Джакарты
пользуется на самом деле: за 30 дней из 2000 контактов wait_call 1486, result_think 225,
result_next_enrollment 189, result_fail 177, result_event 21. Остальные пять — contract,
study_yes, material, waiting_registration, interview — не встретились ни разу, и писать в них
значит класть лида в корзину, которую никто не открывает.

Ответственность за оформление договора лежит на менеджере: `contract` Степан не ставит.

Каждая строка отвечает на два разных вопроса, и их нельзя путать:
  goal      — о чём говорить, когда лид пишет САМ. Идёт в промт каждый ход.
  initiates — должен ли Степан заговорить первым. Почти всегда нет.

Текст блока написан по-русски, и до 03.08.2026 он в таком виде уходил в промт КАЖДОГО филиала:
владелец пишет политику на своём языке, а модель на русском её понимает. Английский филиал
получал абзац кириллицей посреди английского контракта. Русский остаётся дефолтом для `id`
(байт в байт то, что читает боевой филиал), для остальных языков — английский перевод.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_HEADERS = {
    "id": "## ЧТО ПРОИСХОДИТ У МЕНЕДЖЕРА (из CRM)",
    "en": "## WHAT THE MANAGER IS DOING WITH THIS LEAD (from the CRM)",
}
_LAST_CONTACT = {
    "id": "Последний контакт менеджера{who}{stamp}: {status}.",
    "en": "The manager's last contact{who}{stamp}: {status}.",
}
_NEXT_CONTACT = {
    "id": "Следующий контакт запланирован на {date} — до этой даты сам не пиши.",
    "en": "The next contact is scheduled for {date} — do not write first before that date.",
}
_FALLBACK = "en"


def _line(table: dict[str, str], lang: str | None) -> str:
    return table.get((lang or "").lower()) or table[_FALLBACK]


@dataclass(frozen=True)
class Policy:
    goals: dict[str, str] = field(default_factory=dict)
    initiates: bool = False

    def goal(self, lang: str | None) -> str:
        return _line(self.goals, lang)


# Значения приходят из CRM как есть; wait_call без префикса, остальные с result_.
POLICIES: dict[str, Policy] = {
    "wait_call": Policy(
        goals={
            "id": "Менеджер уже говорил с этим человеком по телефону и ждёт следующего "
                  "созвона. Сам первым не пиши. Если человек написал — отвечай обычно и по "
                  "делу, не пересказывай ему, что было на звонке, и не выясняй заново то, что "
                  "он уже обсудил с менеджером.",
            "en": "The manager has already spoken to this person by phone and is waiting for "
                  "the next call. Do not write first. If they write, answer normally and to "
                  "the point — don't retell them what happened on the call, and don't re-ask "
                  "what they already went through with the manager.",
        },
    ),
    "result_think": Policy(
        goals={
            "id": "Менеджер поговорил, человек взял паузу на подумать. Не дави и не продавай "
                  "заново. Твоя задача одна: выяснить, сколько времени ему нужно, и назвать "
                  "конкретный день, когда вернуться к разговору. Если срок не называет — "
                  "предложи вернуться через месяц.",
            "en": "The manager spoke to them and they asked for time to think. Do not push "
                  "and do not sell it again. You have one job: find out how much time they "
                  "need and name a specific day to come back to the conversation. If they "
                  "name no date, suggest coming back in a month.",
        },
        initiates=True,
    ),
    "result_next_enrollment": Policy(
        goals={
            "id": "Человеку не подходит текущий набор, он интересуется следующим. Выясни, "
                  "какой именно набор или направление ему нужно, и договорись вернуться к "
                  "разговору ближе к его старту. Не уговаривай пойти в ближайшую группу.",
            "en": "The current intake doesn't suit them; they're interested in the next one. "
                  "Find out which intake or programme they actually want, and agree to come "
                  "back closer to its start. Do not talk them into the nearest group.",
        },
        initiates=True,
    ),
    "result_fail": Policy(
        goals={
            "id": "Разговор с менеджером закончился отказом. Не продавай. Спроси, почему не "
                  "подошло, и разбери по-настоящему: цена, формат, время, сомнение в "
                  "результате или человек вообще не понял, что за услуга. Один честный вопрос "
                  "за раз. Если он не отвечает — попрощайся тепло и оставь его в покое.",
            "en": "The conversation with the manager ended in a no. Do not sell. Ask why it "
                  "didn't fit, and get to the real answer: price, format, timing, doubt about "
                  "the result, or simply not understanding what the service is. One honest "
                  "question at a time. If they don't answer, say goodbye warmly and leave "
                  "them alone.",
        },
        initiates=True,
    ),
    "result_event": Policy(
        goals={
            "id": "Человек собирается прийти на мероприятие. Ничего специально не делай, "
                  "просто будь полезен, если он напишет.",
            "en": "They are planning to come to an event. Do nothing in particular — just be "
                  "useful if they write.",
        },
    ),
}


def policy_for(status: str | None) -> Policy | None:
    return POLICIES.get((status or "").strip())


def crm_state_block(
    status: str | None, *, manager: str | None = None, when: str | None = None,
    next_contact_at: str | None = None, lang: str = "id",
) -> str | None:
    """Блок в промт: что менеджер сделал и о чём Степану теперь говорить.

    Кладётся рядом с заметкой менеджера и НЕ заменяет её: заметка написана человеком под
    конкретного лида и всегда сильнее таблицы."""
    policy = policy_for(status)
    if policy is None:
        return None
    lines = [_line(_HEADERS, lang)]
    lines.append(_line(_LAST_CONTACT, lang).format(
        who=f" ({manager})" if manager else "",
        stamp=f", {when[:10]}" if when else "",
        status=status))
    if next_contact_at:
        lines.append(_line(_NEXT_CONTACT, lang).format(date=next_contact_at[:10]))
    lines.append(policy.goal(lang))
    return "\n".join(lines)

"""Кто мы для sender: их проект и филиал ↔ наш канал.

Отдельный модуль, потому что у этих чисел два независимых применения и одна цена ошибки.
Во ВХОДЯЩЕМ колбеке они говорят, чей это лид, — ошибись, и человек попадёт в чужой филиал.
В ИСХОДЯЩЕЙ отправке они адресуют сообщение — ошибись, и ответ уйдёт от имени другого
бизнеса. Числа те же, поэтому и живут в одном месте, а не двумя копиями по краям.

Джакарта: project alias `crm`, числовой project_id `6`, branch_id `435` (Виктор, 05.08.2026).
Хранится в настройках канала, а не константой в коде: филиалов станет больше одного, и
второй арендатор не должен требовать правки исходников.
"""
from __future__ import annotations

from dataclasses import dataclass

# Ключи настроек канала. Префикс общий — по нему панель канала понимает, чьи это поля.
PREFIX = "sender."
KEY_PROJECT = PREFIX + "project"
KEY_PROJECT_ID = PREFIX + "project_id"
KEY_BRANCH_ID = PREFIX + "branch_id"


@dataclass(frozen=True)
class SenderTenant:
    """Адрес филиала в системе sender.

    `project` — буквенный alias для MCP (`crm`), `project_id` — то же самое числом, как
    приходит в колбеке (`6`). Хранятся оба, потому что sender ждёт разные в разных местах:
    инструмент отправки принимает alias, колбек присылает число. Выводить одно из другого
    значило бы завести таблицу соответствий, которой у нас нет.
    """

    project: str
    project_id: str
    branch_id: str

    @property
    def configured(self) -> bool:
        return bool(self.project.strip() and self.project_id.strip() and self.branch_id.strip())

    def owns(self, payload: dict) -> bool:
        """Наш ли это лид.

        Сравнение строками: колбек присылает form-urlencoded, где всё — текст, а в настройках
        число могло быть введено человеком. `int()` тут споткнулся бы на пустой строке и на
        значении с пробелом, а обе ситуации означают «не совпало», а не «упасть».
        """
        return (str(payload.get("project_id", "")).strip() == self.project_id.strip()
                and str(payload.get("branch_id", "")).strip() == self.branch_id.strip())

    @staticmethod
    def _num(value: object) -> int | None:
        """Текст настройки → число, как требует схема sender.

        Их инструменты объявляют branchId, id и userId целыми, а у нас это поля ввода и
        поля колбека — то есть строки. Живой вызов отвечает на строку не «неверный тип», а
        отказом всего запроса, и поймать это на заглушке нельзя: она приняла бы что угодно.
        Мусор превращаем в None, а не в ноль: пропущенный необязательный параметр честнее
        выдуманного идентификатора.
        """
        text = str(value or "").strip()
        return int(text) if text.isdigit() else None

    def send_args(self, *, chat_id: str, conversation_id: str,
                  user_id: str | None, text: str) -> dict:
        """Параметры для `sender_conversation_send`, в типах их схемы.

        Обязательны только project, branchId и text — остальное адресация, и каждое поле
        добавляется, лишь когда оно есть: слать null там, где схема допускает пропуск,
        значит спорить с чужой валидацией без нужды.
        """
        args: dict = {
            "project": self.project,
            "branchId": self._num(self.branch_id),
            "text": text,
        }
        if (chat := self._num(chat_id)) is not None:
            args["id"] = chat
        if conversation_id:
            args["conversationId"] = conversation_id
        if (user := self._num(user_id)) is not None:
            args["userId"] = user
        return args

    def inbound_args(self, channel: str) -> dict:
        """Параметры выборки для `sender_inbound_since` — там branchId тоже целое."""
        args: dict = {"project": self.project, "channel": channel}
        if (branch := self._num(self.branch_id)) is not None:
            args["branchId"] = branch
        return args


def from_settings(get: object) -> SenderTenant:
    """Собрать адрес из настроек канала. `get` — callable(key, default) панели настроек."""
    read = get if callable(get) else (lambda k, d="": d)
    return SenderTenant(
        project=str(read(KEY_PROJECT, "") or ""),
        project_id=str(read(KEY_PROJECT_ID, "") or ""),
        branch_id=str(read(KEY_BRANCH_ID, "") or ""),
    )

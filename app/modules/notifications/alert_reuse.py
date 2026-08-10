"""Что делать с алертом, который уже висит в топике лида.

Алерты сыпались потоком: каждое новое событие по одному и тому же человеку добавляло ещё
одно сообщение, и топик превращался в ленту, которую перестают читать. При этом два случая
различаются принципиально, и различать их умеет только состояние переписки:

  менеджер ОТВЕТИЛ после прошлого алерта → тот алерт отработан, он больше не задача.
      Новое событие — это новая задача, и старую карточку надо убрать, а не оставлять
      рядом: две карточки подряд читаются как «два дела», хотя дело одно.

  менеджер НЕ ответил → прошлая карточка всё ещё висит невыполненной. Второе сообщение о
      том же человеке ничего не добавляет — оно только отодвигает первое вверх. Правильно
      переписать то, что уже на экране, свежим содержимым.

Решение отделено от отправки нарочно: оно зависит только от двух дат и одного id, и
проверяется без Telegram и без базы.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AlertAction(StrEnum):
    SEND = "send"      # нечего переиспользовать — обычная отправка
    EDIT = "edit"      # висит неотработанная карточка — переписать её
    REPLACE = "replace"  # менеджер ответил — убрать старую, прислать новую


@dataclass(frozen=True)
class ReusePlan:
    action: AlertAction
    message_id: int | None = None


def plan_alert(
    *,
    previous_message_id: int | None,
    previous_at: datetime | None,
    manager_replied_at: datetime | None,
) -> ReusePlan:
    """Как поступить с новым алертом по лиду, у которого уже есть карточка в топике.

    `manager_replied_at` — время последнего сообщения ЧЕЛОВЕКА в этом треде (не бота).
    Сравнивается с моментом прошлого алерта: ответ, написанный ДО него, ничего про него
    не говорит.
    """
    if previous_message_id is None or previous_at is None:
        return ReusePlan(AlertAction.SEND)
    if manager_replied_at is not None and manager_replied_at > previous_at:
        return ReusePlan(AlertAction.REPLACE, previous_message_id)
    return ReusePlan(AlertAction.EDIT, previous_message_id)

"""Одна карточка на одно дело, а не лента одинаковых.

Алерты по одному лиду шли потоком: каждое событие добавляло сообщение в топик, и топик
переставали читать. Различать два случая умеет только состояние переписки, и это правило
здесь проверяется без Telegram и без базы — оно зависит от двух дат и одного id.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.notifications.alert_reuse import AlertAction, plan_alert

NOW = datetime.now(UTC).replace(tzinfo=None)
HOUR = timedelta(hours=1)


def test_nothing_to_reuse_is_a_plain_send() -> None:
    plan = plan_alert(previous_message_id=None, previous_at=None, manager_replied_at=None)
    assert plan.action is AlertAction.SEND
    assert plan.message_id is None


def test_an_unanswered_card_is_rewritten_not_repeated() -> None:
    """Менеджер прошлую карточку не отработал — вторая ничего не добавляет, только
    отодвигает первую вверх."""
    plan = plan_alert(previous_message_id=42, previous_at=NOW - HOUR, manager_replied_at=None)
    assert plan.action is AlertAction.EDIT
    assert plan.message_id == 42


def test_a_card_the_manager_answered_is_retired() -> None:
    """Ответил — значит то дело закрыто. Новое событие это новая задача, а старая карточка
    рядом с ней читается как второе дело, хотя дело одно."""
    plan = plan_alert(previous_message_id=42, previous_at=NOW - HOUR,
                      manager_replied_at=NOW - timedelta(minutes=10))
    assert plan.action is AlertAction.REPLACE
    assert plan.message_id == 42


def test_a_reply_written_before_the_card_does_not_count() -> None:
    """Ответ ДО прошлого алерта ничего про него не говорит — карточка всё ещё висит."""
    plan = plan_alert(previous_message_id=42, previous_at=NOW - HOUR,
                      manager_replied_at=NOW - timedelta(hours=3))
    assert plan.action is AlertAction.EDIT

"""Страница «Стратегия» собирается из живого кода, а не из нарисованной картинки.

Смысл теста ровно один: если кто-то поменяет порог или расписание, страница обязана показать
новое число. Нарисованная схема протухла бы за неделю — только 30.07.2026 условия передачи
менялись трижды.
"""
from __future__ import annotations

from app.api._ui_strategy import strategy_page_html
from app.modules.crm.policy import POLICIES
from app.modules.crm.push_mcp import DRAIN_BATCH, HANDOFF_WINDOW_DAYS


def test_the_page_renders_the_whole_turn() -> None:
    html = strategy_page_html()
    for heading in ("Сообщение попадает к нам", "Отбор на ответ", "Решение до генерации",
                    "Проверки после генерации", "Очередь и отправка", "Стадия и передача",
                    "Что делает CRM в ответ"):
        assert heading in html


def test_live_constants_are_read_not_retyped() -> None:
    html = strategy_page_html()
    assert f"{HANDOFF_WINDOW_DAYS} дней" in html
    assert str(DRAIN_BATCH) in html


def test_every_policy_is_shown() -> None:
    html = strategy_page_html()
    for status in POLICIES:
        assert status in html


def test_the_silence_table_states_the_split_that_cost_us_most() -> None:
    """Менеджер ведёт лида — отвечаем, но не пишем первыми. До 30.07 это было одно действие,
    и лид на 72 часа переставал получать ответы вообще."""
    html = strategy_page_html()
    assert "Менеджер ведёт лида" in html
    assert "Лид заблокирован" in html
    assert "Канал выключен" in html


def test_no_external_assets() -> None:
    """Админка ничего не тянет с чужих доменов — схема нарисована на CSS."""
    html = strategy_page_html()
    assert "http://" not in html
    assert "cdn" not in html.lower()

"""Страница «Стратегия»: блок-схема из живого кода, на языке интерфейса.

Смысл теста ровно один: если кто-то поменяет порог или расписание, страница обязана показать
новое число. Нарисованная схема протухла бы за неделю — только 30.07.2026 условия передачи
менялись трижды.
"""
from __future__ import annotations

import pytest

from app.api._i18n import _lang, t
from app.api._ui_strategy import strategy_page_html
from app.modules.conversation.reactivation import BATCH_PER_RUN
from app.modules.crm.policy import POLICIES
from app.modules.crm.push_mcp import DRAIN_BATCH, HANDOFF_WINDOW_DAYS


def test_it_is_a_diagram_not_a_list_of_boxes() -> None:
    html = strategy_page_html()
    assert "<svg" in html and "polygon" in html      # стрелки нарисованы, а не подразумеваются
    assert html.count("<rect") >= 7                  # шаги хода


def test_live_constants_are_read_not_retyped() -> None:
    html = strategy_page_html()
    assert str(HANDOFF_WINDOW_DAYS) in html
    assert str(DRAIN_BATCH) in html
    assert str(BATCH_PER_RUN) in html


@pytest.mark.parametrize("lang", ["ru", "en", "id"])
def test_the_page_follows_the_interface_language(lang: str) -> None:
    _lang.set(lang)
    html = strategy_page_html()
    assert t("stg.title") in html
    assert t("stg.sil.title") in html
    # и не подмешивает другой язык в заголовки
    other = {"ru": "en", "en": "id", "id": "ru"}[lang]
    _lang.set(other)
    foreign = t("stg.sil.title")
    _lang.set(lang)
    if foreign != t("stg.sil.title"):
        assert foreign not in html


def test_every_policy_is_shown() -> None:
    _lang.set("ru")
    html = strategy_page_html()
    for status in POLICIES:
        assert status in html


def test_the_silence_table_states_the_split_that_cost_us_most() -> None:
    """Менеджер ведёт лида — отвечаем, но не пишем первыми. До 30.07 это было одним
    действием, и лид на 72 часа переставал получать ответы вообще."""
    _lang.set("ru")
    html = strategy_page_html()
    assert t("stg.s.manager") in html
    assert t("stg.s.blocked") in html
    assert t("stg.s.channel") in html


def test_no_external_assets() -> None:
    """Админка ничего не тянет с чужих доменов — схема нарисована инлайновым SVG."""
    html = strategy_page_html()
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()

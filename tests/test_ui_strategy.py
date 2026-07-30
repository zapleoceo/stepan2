"""Страница «Стратегия»: детальная блок-схема из живого кода, на языке интерфейса.

Смысл теста ровно один: если кто-то поменяет порог, расписание или порядок проверок, страница
обязана показать новое. Нарисованная схема протухла бы за неделю — только 30.07.2026 условия
передачи менялись трижды.
"""
from __future__ import annotations

import pytest

from app.api._i18n import _lang, t
from app.api._ui_strategy import _CHECKS, strategy_page_html
from app.modules.conversation.reactivation import BATCH_PER_RUN
from app.modules.crm.policy import POLICIES
from app.modules.crm.push_mcp import DRAIN_BATCH, HANDOFF_WINDOW_DAYS


def test_every_check_is_a_diamond_with_both_answers() -> None:
    """Не колонка карточек: у каждой проверки должен быть выход «да» и выход «нет»."""
    _lang.set("ru")
    html = strategy_page_html()
    assert html.count("polygon") >= len(_CHECKS)
    yes, no = t("stg.yes"), t("stg.no")
    assert html.count(f">{yes}<") >= len(_CHECKS)      # ветка «да» у каждой
    assert html.count(f">{no}<") >= len(_CHECKS) - 1   # «нет» ведёт к следующей


def test_every_check_states_what_happens_on_yes() -> None:
    _lang.set("ru")
    html = strategy_page_html()
    for _q, term_key, _kind in _CHECKS:
        assert t(term_key) in html


def test_the_order_matches_the_code_not_a_drawing() -> None:
    """Блокировка проверяется раньше всего, передача в CRM — в конце."""
    keys = [q for q, _t, _k in _CHECKS]
    assert keys[0] == "stg.q.blocked"
    assert keys.index("stg.q.won") < keys.index("stg.q.owner")
    assert keys[-1] == "stg.q.manager"


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
    assert t("stg.q.blocked") in html


def test_it_scrolls_and_zooms() -> None:
    html = strategy_page_html()
    assert "stg-wrap" in html and "overflow:auto" in html
    assert "stg-zoom" in html
    assert "wheel" in html and "scale(" in html


def test_every_policy_is_shown() -> None:
    _lang.set("ru")
    html = strategy_page_html()
    for status in POLICIES:
        assert status in html


def test_no_external_assets() -> None:
    """Админка ничего не тянет с чужих доменов — схема и зум свои."""
    html = strategy_page_html()
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()

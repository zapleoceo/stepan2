"""Страница «Стратегия»: подробные блок-схемы из живого кода, на языке интерфейса.

Смысл теста один: если кто-то поменяет порог, расписание или порядок проверок, страница
обязана показать новое. Нарисованная схема протухла бы за неделю — только 30.07.2026 условия
передачи менялись трижды.
"""
from __future__ import annotations

import pytest

from app.api._i18n import _lang, t
from app.api._ui_strategy import _CRM_CHECKS, _TURN_CHECKS, _wrap, strategy_page_html
from app.modules.conversation.reactivation import BATCH_PER_RUN
from app.modules.crm.policy import POLICIES
from app.modules.crm.push_mcp import DRAIN_BATCH, HANDOFF_WINDOW_DAYS

_ALL = _TURN_CHECKS + _CRM_CHECKS


def test_the_page_scrolls_with_the_page_not_inside_a_box() -> None:
    """Живая жалоба 30.07.2026: низ страницы было не достать. Вложенный контейнер с
    ограниченной высотой съедал прокрутку — вертикаль должна листаться вместе со страницей,
    свой скролл остаётся только горизонтальным."""
    html = strategy_page_html()
    assert "max-height" not in html
    assert "overflow-y:visible" in html
    assert "overflow-x:auto" in html


def test_zoom_resizes_the_svg_rather_than_transforming_it() -> None:
    """scale() не меняет место, которое занимает элемент, поэтому страница не узнала бы, что
    схема выросла, и до низа снова было бы не долистать."""
    html = strategy_page_html()
    assert "setAttribute('width'" in html
    assert "scale(" not in html


def test_every_check_is_a_diamond_with_both_answers() -> None:
    _lang.set("ru")
    html = strategy_page_html()
    yes, no = t("stg.yes"), t("stg.no")
    assert html.count("polygon") >= len(_ALL)
    assert html.count(f">{yes}<") >= len(_ALL)
    assert html.count(f">{no}<") >= len(_ALL)


def test_every_check_states_what_happens_on_yes() -> None:
    """Сверяем по первой строке переноса: длинный текст режется на несколько <text>."""
    _lang.set("ru")
    html = strategy_page_html()
    for _q, term_key, _kind in _ALL:
        assert _wrap(t(term_key), 42)[0] in html


def test_the_crm_side_is_its_own_chart_not_one_box() -> None:
    """Раньше передача была одним узлом, по которому нельзя понять ни момента, ни
    содержимого."""
    _lang.set("ru")
    html = strategy_page_html()
    assert html.count("stg-scroll") >= 2
    assert t("stg.crm.title") in html
    for key in ("stg.c.q.phone", "stg.c.q.same", "stg.c.q.human"):
        assert t(key) in html
    # и три пути названы поимённо, с маркерами
    assert "crm_pushed_handoff" in html and "crm_pushed:" in html


def test_the_order_matches_the_code_not_a_drawing() -> None:
    keys = [q for q, _t, _k in _TURN_CHECKS]
    assert keys[0] == "stg.q.blocked"
    assert keys.index("stg.q.won") < keys.index("stg.q.owner")
    assert keys[-1] == "stg.q.manager"
    crm = [q for q, _t, _k in _CRM_CHECKS]
    assert crm[0] == "stg.c.q.phone"        # без телефона не уедет ничем


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
    assert t("stg.crm.title") in html


def test_every_policy_is_shown() -> None:
    _lang.set("ru")
    html = strategy_page_html()
    for status in POLICIES:
        assert status in html


def test_no_external_assets() -> None:
    html = strategy_page_html()
    assert "http://" not in html
    assert "https://" not in html
    assert "cdn" not in html.lower()

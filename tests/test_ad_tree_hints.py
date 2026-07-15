"""Column hints on the ad tree.

This table shows Meta's numbers next to ours and they deliberately do NOT reconcile (Meta
counts taps, we count leads). Without saying that on the column itself, the gap reads as a
bug. These tests pin the hints to the columns that actually mislead, and pin the silence for
columns with no hint defined — a header showing the raw key 'rep.won.hint' would be worse
than no hint at all.
"""
from __future__ import annotations

from app.api._i18n import _lang, t
from app.api._ui_panels import _ad_tree_html, _col_hint

ROW = ("igad-1", "3931661706982573994", 100, 60, 10, 30)
MAP = {"3931661706982573994": {"ad_id": "ad1", "ad_name": "Ad 1",
                               "campaign_name": "Vibe Coding", "objective": "OUTCOME_ENGAGEMENT"}}
SPEND = {"ad1": {"spend": 300.0, "conv_started": 600, "conv_depth_5": 40, "blocks": 12}}


def _html() -> str:
    _lang.set("ru")
    return _ad_tree_html([ROW], MAP, SPEND)


def test_hint_renders_as_title_attribute() -> None:
    _lang.set("ru")
    assert _col_hint("rep.ads_cpl").startswith(' title="')


def test_no_hint_defined_means_no_title_not_a_raw_key() -> None:
    _lang.set("ru")
    assert _col_hint("rep.nonexistent_column") == ""


def test_hint_is_escaped() -> None:
    _lang.set("ru")
    assert "<" not in _col_hint("rep.ads_started").replace(' title="', "")


def test_taps_column_warns_it_is_not_conversations() -> None:
    """The single most misleading number on the page — it must say so itself."""
    _lang.set("ru")
    hint = t("rep.ads_started.hint")
    assert "ТАПЫ" in hint
    assert "не сходится" in hint.lower() or "не должно" in hint


def test_cost_per_lead_hint_explains_the_formula() -> None:
    _lang.set("ru")
    assert "÷" in t("rep.ads_cpl.hint")


def test_coverage_hint_says_missing_spend_is_excluded() -> None:
    """Coverage < 100% means totals under-report spend; the hint must not hide that."""
    _lang.set("ru")
    hint = t("rep.ads_coverage.hint")
    assert "не попадает" in hint or "неизвестен" in hint


def test_hints_reach_the_rendered_tree() -> None:
    html = _html()
    assert t("rep.ads_started.hint")[:30] in html   # column header hint
    assert t("rep.ads_row.hint")[:30] in html       # campaign summary hint
    assert t("rep.ads_coverage.hint")[:30] in html  # coverage note hint


def test_hinted_elements_are_discoverable() -> None:
    """A title= with no visual affordance is a hint nobody ever finds."""
    html = _html()
    assert 'class="rep-sort help"' in html
    assert "cursor:help" in html


def test_hints_exist_in_every_supported_language() -> None:
    keys = ["rep.ads_started.hint", "rep.ads_leads.hint", "rep.ads_cpl.hint",
            "rep.ads_coverage.hint", "rep.ads_row.hint"]
    for lang in ("ru", "en", "id"):
        _lang.set(lang)
        for key in keys:
            assert t(key) != key, f"{key} missing for {lang}"

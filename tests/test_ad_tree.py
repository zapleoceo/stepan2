"""Campaign → ad tree on the reports panel.

The risky parts are arithmetic, not markup: spend must not be double-counted when several
media resolve to one ad, and leads with no ad must stay visible instead of quietly shrinking
the base (which would make the spend view look more complete than it is).
"""
from __future__ import annotations

from app.api._i18n import _lang
from app.api._ui_panels import _ad_tree_html

# (ad_id, ad_media_id, total, pipeline, won, dormant) — the fetch_ad_funnel row shape.
ROW_A = ("igad-1", "3931661706982573994", 100, 60, 10, 30)
ROW_B = ("igad-2", "3932264179182956279", 50, 20, 5, 25)
ROW_ORPHAN = ("igad-9", "3902640133392596802", 40, 10, 1, 29)

MAP = {
    "3931661706982573994": {"ad_id": "ad1", "ad_name": "Ad 1",
                            "campaign_name": "Vibe Coding", "objective": "OUTCOME_ENGAGEMENT"},
    "3932264179182956279": {"ad_id": "ad2", "ad_name": "Ad 2",
                            "campaign_name": "SMM", "objective": "OUTCOME_TRAFFIC"},
}
SPEND = {
    "ad1": {"spend": 300.0, "conv_started": 600, "conv_depth_5": 40, "blocks": 12},
    "ad2": {"spend": 100.0, "conv_started": 200, "conv_depth_5": 5, "blocks": 0},
}


def _html(rows, mapping=None, spend=None) -> str:
    _lang.set("en")
    return _ad_tree_html(rows, mapping if mapping is not None else MAP,
                         spend if spend is not None else SPEND)


def test_groups_ads_under_their_campaign() -> None:
    html = _html([ROW_A, ROW_B])
    assert "Vibe Coding" in html
    assert "SMM" in html
    assert html.count('class="adt-c') == 2


def test_campaign_summary_shows_cost_per_our_lead_not_per_conversation() -> None:
    """$300 over 100 leads WE hold = $3.00 — not $300/600 taps = $0.50."""
    html = _html([ROW_A])
    assert "$3.00" in html
    assert "$0.50" not in html


def test_spend_counted_once_when_two_media_map_to_the_same_ad() -> None:
    second_medium = ("igad-1b", "3932267938260790752", 20, 5, 2, 13)
    mapping = dict(MAP)
    mapping["3932267938260790752"] = MAP["3931661706982573994"]  # same ad1
    html = _ad_tree_html([ROW_A, second_medium], mapping, SPEND)
    _lang.set("en")
    # 120 leads on ONE ad's $300 → $2.50; double-counting spend would render $5.00.
    assert "$2.50" in html
    assert "$5.00" not in html


def test_unmatched_leads_get_their_own_group_and_are_not_dropped() -> None:
    html = _html([ROW_A, ROW_ORPHAN])
    assert "Not matched to an ad" in html
    assert "spend unknown" in html
    assert html.count('class="adt-c') == 2


def test_coverage_percentage_is_reported() -> None:
    html = _html([ROW_A, ROW_ORPHAN])
    assert "71% (100/140)" in html  # 100 matched of 140 leads seen


def test_coverage_is_100_when_everything_maps() -> None:
    assert "100% (150/150)" in _html([ROW_A, ROW_B])


def test_campaigns_ordered_by_spend() -> None:
    html = _html([ROW_B, ROW_A])
    assert html.index("Vibe Coding") < html.index("SMM")  # $300 before $100


def test_blocks_surface_on_the_campaign_row() -> None:
    assert ">12<" in _html([ROW_A])


def test_no_rows_renders_nothing() -> None:
    assert _html([]) == ""


def test_renders_without_any_mapping_yet() -> None:
    """Before the first sync the map is empty — the funnel must still be usable."""
    html = _html([ROW_A, ROW_B], mapping={}, spend={})
    assert "Not matched to an ad" in html
    assert "0% (0/150)" in html

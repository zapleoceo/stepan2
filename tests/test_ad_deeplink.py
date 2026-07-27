"""Where an ad row's "open the ad" link points.

Two id spaces meet in this table and only one of them Ads Manager can resolve. A bridged ad
carries a real Marketing API id living in the configured account, so it deep-links into the
account — editable, with spend. An unbridged row only has the Ad Library id instagrapi
reports; sending that to Ads Manager lands on 'not found', so it must stay on the Library.
"""
from __future__ import annotations

from app.api._i18n import _lang
from app.api._ui_panels import _ad_tree_html, _ads_manager_url

ROW = ("igad-1", "3931661706982573994", 100, 60, 10, 30, 2)
ORPHAN = ("igad-9", "3902640133392596802", 40, 10, 1, 29, 0)
MAP = {
    "3931661706982573994": {
        "ad_id": "120251248019540560", "ad_name": "Ad 1", "campaign_name": "Vibe Coding",
        "objective": "OUTCOME_ENGAGEMENT",
        "campaign_id": "120251248019530560", "adset_id": "120251248019520560",
    },
}
SPEND = {"120251248019540560": {"spend": 300.0, "conv_started": 600, "conv_depth_5": 40}}
ACT, BID = "1000480912055519", "949920286532207"


def _html(rows, account_id: str = ACT) -> str:
    _lang.set("en")
    return _ad_tree_html(rows, MAP, SPEND, None, BID, account_id)


def test_bridged_ad_deep_links_into_ads_manager_with_all_three_selectors() -> None:
    html = _html([ROW])
    assert "adsmanager.facebook.com/adsmanager/manage/ads?" in html
    assert f"act={ACT}" in html
    assert "selected_ad_ids=120251248019540560" in html
    # Ads Manager filters by selector; campaign+adset land on the ad's own row, not the list.
    assert "selected_campaign_ids=120251248019530560" in html
    assert "selected_adset_ids=120251248019520560" in html


def test_bridged_ad_uses_the_marketing_api_id_not_the_instagrapi_one() -> None:
    html = _html([ROW])
    assert "selected_ad_ids=igad-1" not in html


def test_unbridged_ad_keeps_the_ad_library_and_gets_no_manager_link() -> None:
    html = _ad_tree_html([ORPHAN], {}, {}, None, BID, ACT)
    assert "facebook.com/ads/library/?id=igad-9" in html
    assert "adsmanager.facebook.com" not in html


def test_without_a_configured_account_there_is_nothing_to_scope_to() -> None:
    html = _html([ROW], account_id="")
    assert "adsmanager.facebook.com" not in html
    assert "facebook.com/ads/library/" in html


def test_the_ad_library_entry_survives_alongside_the_manager_entry() -> None:
    """The ad may have been published from another account; the Library always resolves it."""
    html = _html([ROW])
    assert "facebook.com/ads/library/?id=120251248019540560" in html


def test_url_builder_omits_selectors_it_was_not_given() -> None:
    url = _ads_manager_url("A1", ACT)
    # HTML-escaped: the URL is rendered straight into an href, so & must be &amp;.
    assert url.endswith(f"act={ACT}&amp;selected_ad_ids=A1")
    assert "selected_campaign_ids" not in url
    assert "selected_adset_ids" not in url

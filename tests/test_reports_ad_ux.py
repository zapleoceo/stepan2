"""Reports ad-funnel UX: the operator product-mapping cell, the ad-action menu, the
chat-list ad filter, and the upsert route guard. Pure HTML generators + render smoke tests."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient  # noqa: E402

from app.api._ui_panels import _ad_funnel_html, admap_cell_inner  # noqa: E402
from app.api.main import app  # noqa: E402

_PRODUCTS = [("vibe_coding", "Vibe Coding"), ("smm_intensive", "SMM Intensive")]
_ROWS = [("AD1", "3932267938260790752", 10, 4, 3, 3), ("AD2", None, 5, 2, 1, 2)]


def _set_lang(code: str = "en") -> None:
    from app.api._i18n import _lang
    _lang.set(code)


# ─── mapping cell ─────────────────────────────────────────────────────────────

def test_admap_cell_marks_mapped_option_selected() -> None:
    _set_lang()
    html = admap_cell_inner("AD1", "vibe_coding", None, _PRODUCTS)
    assert '<select class="admap-sel"' in html
    assert 'value="vibe_coding" selected>Vibe Coding' in html
    assert "admap-sug" not in html  # already mapped → no suggestion chip


def test_admap_cell_shows_suggestion_when_unmapped() -> None:
    _set_lang()
    html = admap_cell_inner("AD2", None, "smm_intensive", _PRODUCTS)
    assert "admap-sug" in html
    assert '"product":"smm_intensive"' in html
    assert "SMM Intensive" in html
    assert " selected>" not in html  # nothing persisted yet


def test_admap_cell_plain_when_unmapped_no_suggestion() -> None:
    _set_lang()
    html = admap_cell_inner("AD3", None, None, _PRODUCTS)
    assert '<select class="admap-sel"' in html
    assert "admap-sug" not in html


# ─── ad-funnel table ──────────────────────────────────────────────────────────

def test_ad_funnel_with_products_has_mapping_and_menu() -> None:
    _set_lang()
    html = _ad_funnel_html(
        _ROWS, business_id="BID", account_id="ACT",
        mappings={"AD1": "vibe_coding"}, suggestions={"AD2": "smm_intensive"},
        products=_PRODUCTS)
    assert 'class="admap-sel"' in html              # product column present
    assert '<details class="admenu">' in html       # ad-action menu
    assert "/ui/inbox?ad_id=AD1" in html            # "open this ad's chats"
    # the FB link goes to the public Ad Library keyed by ad id (resolves any live ad
    # regardless of ad account), not an account-scoped Ads Manager deep link
    assert "facebook.com/ads/library/?id=AD1" in html
    assert "adsmanager.facebook.com" not in html
    assert 'data-ig="3932267938260790752"' in html   # IG post hover hook (row 1 has media)


def test_ad_funnel_without_products_is_readonly() -> None:
    _set_lang()
    html = _ad_funnel_html(_ROWS, products=None)
    assert 'class="admap-sel"' not in html           # no product column cross-branch
    assert '<details class="admenu">' in html        # menu still available


def test_ad_funnel_empty_rows_render_nothing() -> None:
    assert _ad_funnel_html([], products=_PRODUCTS) == ""


def test_ad_funnel_counts_link_to_filtered_chats() -> None:
    _set_lang()
    # row AD1: total=10, pipeline=4, won=3, dormant=3
    html = _ad_funnel_html([("AD1", None, 10, 4, 3, 3)], products=_PRODUCTS)
    assert '<a class="rep-lnk" href="/ui/inbox?ad_id=AD1">10</a>' in html   # total → all chats
    assert '<a class="rep-lnk" href="/ui/inbox?ad_id=AD1&grp=pipeline">4</a>' in html
    assert '<a class="rep-lnk" href="/ui/inbox?ad_id=AD1&grp=won">3</a>' in html
    assert '<a class="rep-lnk" href="/ui/inbox?ad_id=AD1&grp=dormant">3</a>' in html


def test_inbox_grp_filter_shows_group_chip_and_scoped_load() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ui/inbox?ad_id=120255671613970771&grp=won")
    assert resp.status_code == 200
    assert "ad-filter" in resp.text
    assert "/ui/threads?ad_id=120255671613970771&grp=won" in resp.text


# ─── sort + filter ────────────────────────────────────────────────────────────

def test_ad_funnel_headers_are_sortable() -> None:
    _set_lang()
    html = _ad_funnel_html(_ROWS, products=_PRODUCTS)
    assert 'class="rep-sort"' in html
    assert 'onclick="repSort(this)"' in html
    assert 'data-num="1"' in html                    # numeric columns flagged for numeric sort
    assert "function repSort" in html                # inline handler shipped with the fragment


def test_ad_funnel_has_per_column_filters() -> None:
    _set_lang()
    html = _ad_funnel_html(_ROWS, products=_PRODUCTS)
    assert 'class="rep-fltr"' in html                # filter row present
    assert 'data-f="text"' in html                   # ad-id substring filter
    assert 'data-f="min"' in html                    # numeric ≥ filters
    assert 'data-f="eq"' in html                     # product dropdown filter
    assert 'value="smm_intensive">SMM Intensive' in html  # product options in the filter
    assert "function repFilter" in html


def test_ad_funnel_readonly_has_sort_and_filter_but_no_product_eq() -> None:
    _set_lang()
    html = _ad_funnel_html(_ROWS, products=None)
    assert 'class="rep-sort"' in html                # still sortable cross-branch
    assert 'data-f="min"' in html                    # numeric filters still present
    assert 'data-f="eq"' not in html                 # no product column → no product filter


# ─── routes ───────────────────────────────────────────────────────────────────

def test_inbox_ad_filter_renders_chip_and_scoped_thread_load() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ui/inbox?ad_id=120255671613970771")
    assert resp.status_code == 200
    assert "ad-filter" in resp.text
    assert "120255671613970771" in resp.text
    assert "/ui/threads?ad_id=120255671613970771" in resp.text


def test_inbox_segment_filter_renders_chip_and_scoped_thread_load() -> None:
    # Clicking a segment-tree leaf opens /ui/inbox?lead_type=warm — chip + scoped thread load.
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/ui/inbox?lead_type=warm")
    assert resp.status_code == 200
    assert "ad-filter" in resp.text                       # reuses the dismissable chip styling
    assert "/ui/threads?lead_type=warm" in resp.text      # thread list loads scoped to segment


def test_ad_product_map_rejects_without_single_branch() -> None:
    # No branch cookie → branch_ids is None → the route refuses before any DB work.
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/ui/ads/AD1/product", data={"product": "vibe_coding"})
    assert resp.status_code == 400

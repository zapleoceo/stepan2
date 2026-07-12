"""Help-mode (the ? button) coverage: Settings fields and Branch form fields must carry
data-help so hovering in help mode explains them."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.api._i18n import _lang, t  # noqa: E402
from app.api._ui_panels import branch_edit_html  # noqa: E402
from app.api._ui_settings import field_html  # noqa: E402
from app.modules.settings import schema as S  # noqa: E402


def test_branch_form_fields_carry_help_hints() -> None:
    _lang.set("ru")
    html = branch_edit_html(1, "Jakarta", "id", 7, True, other_branches=[(2, "KL")])
    # name, lang, tz, active, kb-source → 5 hinted field groups
    assert html.count("data-help=") == 5
    assert t("br.tz_h")[:20] in html          # tz field explains it's branch-local analytics


def test_settings_field_carries_help_hint() -> None:
    _lang.set("ru")
    f = next(fld for fld in S.all_fields() if fld.help)
    html = field_html(f, "", "ru")
    assert "data-help=" in html
    assert S.tr(f.help, "ru")[:15] in html    # the schema help rides into the hint


def test_settings_field_without_help_falls_back_to_label() -> None:
    _lang.set("en")
    f = next((fld for fld in S.all_fields() if not fld.help), None)
    if f is None:  # every field happens to have help — nothing to assert
        return
    html = field_html(f, "", "en")
    assert "data-help=" in html               # still hinted, using the label


def test_broker_log_columns_and_title_are_hinted() -> None:
    from app.api._ui_panels import broker_log_panel_html
    _lang.set("ru")
    html = broker_log_panel_html([], page=0, size=50, total=0)
    # 9 column headers + the section title = 10 hinted anchors, even with no rows
    assert html.count("data-help=") >= 10
    assert t("log.h.cost")[:10] in html       # a column hint made it into a <th>


def test_every_main_panel_title_is_hinted() -> None:
    _lang.set("ru")
    from app.api._ui_members import members_panel_html
    from app.api._ui_panels import (
        branches_panel_html,
        coach_chat_html,
        leads_panel_html,
        outbox_panel_html,
        products_panel_html,
    )
    assert "data-help=" in leads_panel_html([])
    assert "data-help=" in outbox_panel_html([])
    assert "data-help=" in products_panel_html([])
    assert "data-help=" in coach_chat_html(1, [], [])
    assert "data-help=" in branches_panel_html([])
    assert "data-help=" in members_panel_html([], [])

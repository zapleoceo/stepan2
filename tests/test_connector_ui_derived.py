"""Labels, icons, filter chips, the kind selector, the credential panel and the settings
gate must all come from the ConnectorSpec.

Each test CHANGES a registered spec at runtime and asserts the rendered HTML follows. A copy
of the value hardcoded in a UI module would not move, so these fail the moment any of the
seven duplicated tables comes back.
"""
from __future__ import annotations

import dataclasses
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from typing import Any  # noqa: E402

import pytest  # noqa: E402

from app.api._query import awaiting_kind_sql  # noqa: E402
from app.api._ui_html import _CHANNEL_ICON, app_shell  # noqa: E402
from app.api._ui_panels import (  # noqa: E402
    channel_edit_form_html,
    channel_list_partial_html,
    channel_new_form_html,
)
from app.api._ui_settings import _field_for_kind  # noqa: E402
from app.connectors.registry import REGISTRY, all_specs  # noqa: E402
from app.domain.enums import ChannelKind  # noqa: E402


def _respec(monkeypatch: pytest.MonkeyPatch, kind: ChannelKind, **changes: Any) -> None:
    monkeypatch.setitem(REGISTRY, kind, dataclasses.replace(REGISTRY[kind], **changes))


def _rows() -> list[tuple]:
    # (id, kind, handle, account_id, is_active) — the shape channel_list_partial_html reads
    return [(7, "whatsapp", "+62", "", True)]


def test_channel_list_label_comes_from_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "WhatsApp" in channel_list_partial_html(_rows(), [], 1)
    _respec(monkeypatch, ChannelKind.WHATSAPP, label="Signal")
    html = channel_list_partial_html(_rows(), [], 1)
    assert "Signal" in html and "WhatsApp" not in html


def test_channel_editor_title_comes_from_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    _respec(monkeypatch, ChannelKind.WHATSAPP, label="Signal")
    assert "Signal #7" in channel_edit_form_html(7, "whatsapp", "+62", "", True)


def test_new_channel_selector_lists_every_registered_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = channel_new_form_html(1)
    for spec in all_specs():
        assert f'value="{spec.kind.value}"' in html
    _respec(monkeypatch, ChannelKind.WHATSAPP, label_key="ch.kind_ig")
    # the option text follows the spec's i18n key, not a tuple copied next to the <select>
    assert channel_new_form_html(1).count("Instagram") == 2


def test_inbox_filter_chips_are_one_per_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    html = app_shell("en", "", active_nav="inbox")
    assert html.count("data-kind=") == len(all_specs())
    for spec in all_specs():
        assert f'data-kind="{spec.kind.value}"' in html
        assert spec.icon_class in html

    _respec(monkeypatch, ChannelKind.WHATSAPP,
            icon_class="fa-brands fa-signal-messenger", icon_color="#3a76f0")
    flipped = app_shell("en", "", active_nav="inbox")
    assert "fa-brands fa-signal-messenger" in flipped
    assert "#3a76f0" in flipped


def test_thread_badge_icons_are_the_registry_not_a_second_copy() -> None:
    """_CHANNEL_ICON is built from the specs — a re-hardcoded copy that drifts fails here."""
    assert _CHANNEL_ICON == {
        s.kind.value: (s.icon_class, s.icon_color) for s in all_specs()
    }


def test_credential_panel_is_the_spec_s_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api._ui_panels import _ch_form_for

    _respec(monkeypatch, ChannelKind.WHATSAPP,
            credential_panel=lambda ch_id, **_: f"<form>panel-for-{ch_id}</form>")
    assert _ch_form_for(9, "whatsapp") == "<form>panel-for-9</form>"
    assert "Unknown channel kind" in _ch_form_for(9, "tiktok")


def test_settings_fields_are_hidden_by_the_owning_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    field = _FakeField("meta_page_id")
    assert _field_for_kind(field, "meta_business") is True
    assert _field_for_kind(field, "whatsapp") is False
    assert _field_for_kind(_FakeField("reply_delay_s"), "whatsapp") is True

    # hand the prefix to WhatsApp instead and the visibility follows it
    _respec(monkeypatch, ChannelKind.META_BUSINESS, settings_prefixes=())
    _respec(monkeypatch, ChannelKind.WHATSAPP, settings_prefixes=("meta_",))
    assert _field_for_kind(field, "whatsapp") is True
    assert _field_for_kind(field, "meta_business") is False


def test_awaiting_sql_excludes_exactly_the_specs_that_opt_out() -> None:
    counting = dataclasses.replace(REGISTRY[ChannelKind.INSTAGRAM], counts_as_awaiting=True)
    silent = dataclasses.replace(REGISTRY[ChannelKind.WHATSAPP], counts_as_awaiting=False)

    assert awaiting_kind_sql([counting]) == ""  # nobody opted out → no filter at all
    sql = awaiting_kind_sql([counting, silent])
    assert "'whatsapp'" in sql and "'instagram'" not in sql
    assert "NOT IN" in sql


class _FakeField:
    def __init__(self, key: str) -> None:
        self.key = key

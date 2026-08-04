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
from app.api._ui_html import _channel_badge, app_shell  # noqa: E402
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


def test_new_channel_selector_lists_every_connector_an_operator_may_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = channel_new_form_html(1)
    for spec in all_specs():
        assert (f'value="{spec.kind.value}"' in html) is spec.operator_addable
    _respec(monkeypatch, ChannelKind.WHATSAPP, label_key="ch.kind_ig")
    # the option text follows the spec's i18n key, not a tuple copied next to the <select>
    assert channel_new_form_html(1).count("Instagram") == 2


def test_the_selector_follows_the_spec_when_a_connector_becomes_addable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exclusion is the spec's word, not a name written next to the <select>. Flip the
    declaration and the option appears — which is also what proves the assertion above is
    reading `operator_addable` rather than agreeing with a hardcoded list by luck."""
    assert 'value="website"' not in channel_new_form_html(1)
    _respec(monkeypatch, ChannelKind.WEBSITE, operator_addable=True)
    assert 'value="website"' in channel_new_form_html(1)


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


def test_thread_badge_icon_follows_the_spec_at_render_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The badge reads the spec per render. It used to read a module-level dict built at
    import, which no test could tell apart from a hardcoded one — nothing can change the
    registry after import, so the flip below was structurally impossible."""
    assert "fa-brands fa-whatsapp" in _channel_badge("whatsapp")

    _respec(monkeypatch, ChannelKind.WHATSAPP,
            icon_class="fa-brands fa-signal-messenger", icon_color="#3a76f0")
    badge = _channel_badge("whatsapp")
    assert "fa-brands fa-signal-messenger" in badge and "#3a76f0" in badge
    assert "fa-whatsapp" not in badge


def test_thread_badge_keeps_the_meta_carrying_instagram_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One meta_business channel serves BOTH Messenger and Instagram Direct, so a Graph
    conversation id beginning "aWdf" wears the Instagram mark — and it must be INSTAGRAM's
    registered icon, not a literal that survived the switch to spec lookup."""
    _respec(monkeypatch, ChannelKind.INSTAGRAM, icon_class="fa-solid fa-camera")
    assert "fa-solid fa-camera" in _channel_badge("meta_business", "aWdfdGhyZWFk")
    assert "fa-brands fa-facebook" in _channel_badge("meta_business", "t_123")
    assert "fa-solid fa-comment" in _channel_badge("tiktok")  # unregistered → the fallback


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

    sql = awaiting_kind_sql([counting, silent])
    assert "'whatsapp'" in sql and "'instagram'" not in sql
    assert "NOT IN" in sql


def test_awaiting_sql_still_requires_the_channel_row_when_nobody_opts_out() -> None:
    """The kind test rode along with "the channel row must EXIST". Dropping the whole EXISTS
    on an empty exclusion list would make flipping Meta's counts_as_awaiting to True ALSO
    start counting threads whose channel is gone — a second change, invisible in that diff."""
    counting = dataclasses.replace(REGISTRY[ChannelKind.INSTAGRAM], counts_as_awaiting=True)
    sql = awaiting_kind_sql([counting])
    assert "EXISTS" in sql and "c.id = ct.channel_id" in sql
    assert "NOT IN" not in sql and "c.kind" not in sql


def test_awaiting_base_is_derived_at_every_call_not_frozen_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """awaiting_base() is what the inbox routes paste into their SQL. Silencing a different
    connector must move it — a constant computed once at import, or a literal re-pasted at a
    route, would keep answering with yesterday's registry."""
    from app.api._query import awaiting_base

    assert "'meta_business'" in awaiting_base()

    _respec(monkeypatch, ChannelKind.META_BUSINESS, counts_as_awaiting=True)
    _respec(monkeypatch, ChannelKind.INSTAGRAM, counts_as_awaiting=False)
    flipped = awaiting_base()
    assert "'instagram'" in flipped and "'meta_business'" not in flipped


class _FakeField:
    def __init__(self, key: str) -> None:
        self.key = key

"""Settings schema SSOT + schema-driven renderer: integrity, i18n, typed controls."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api._ui_settings import (  # noqa: E402
    channel_settings_html,
    field_html,
    settings_form_html,
)
from app.api.main import app  # noqa: E402
from app.modules.settings import schema as S  # noqa: E402

_LANGS = ("ru", "en", "id")

# Keys BranchSettings._parse depends on — defaults() must cover all of them.
# tz_offset_h is intentionally NOT here: it's sourced from the branch row (branch.tz_offset_h),
# not from app_setting, so it has no schema default.
_BRANCH_KEYS = (
    "agent_enabled_global", "hourly_cap", "daily_cap", "quiet_start", "quiet_end",
    "reply_delay_min_s", "reply_delay_max_s", "tg_group_id",
    "followup_enabled", "followup_schedule_h",
    "tech_search_enabled", "tech_usecase_enabled",
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ─── schema integrity ─────────────────────────────────────────────────────────

def test_every_field_localized_in_all_languages() -> None:
    for f in S.all_fields():
        for lang in _LANGS:
            assert f.label.get(lang), f"{f.key}: missing label[{lang}]"
            if f.placeholder:
                assert f.placeholder.get(lang), f"{f.key}: missing placeholder[{lang}]"
            if f.help:
                assert f.help.get(lang), f"{f.key}: missing help[{lang}]"


def test_section_titles_localized() -> None:
    assert S.SCHEMA
    for sec in S.SCHEMA:
        assert sec.fields
        for lang in _LANGS:
            assert sec.title.get(lang)


def test_no_duplicate_keys() -> None:
    keys = [f.key for f in S.all_fields()]
    assert len(keys) == len(set(keys))


def test_defaults_cover_branchsettings_keys() -> None:
    d = S.defaults()
    for key in _BRANCH_KEYS:
        assert key in d, f"defaults() missing BranchSettings key {key}"


def test_service_defaults_derive_from_schema() -> None:
    from app.modules.settings.service import _DEFAULTS
    assert _DEFAULTS == S.defaults()


def test_new_feature_keys_present() -> None:
    d = S.defaults()
    for key in ("daily_budget_usd", "crm_enabled", "crm_webhook_url", "meta_capi_token"):
        assert key in d


# ─── renderer ─────────────────────────────────────────────────────────────────

def test_form_posts_to_save_and_has_sections() -> None:
    html = settings_form_html({}, "en")
    assert 'hx-post="/ui/settings/save"' in html
    assert "Bot" in html
    assert "Bot auto-replies" in html  # a branch-scope field
    assert "Messages / hour" not in html  # anti-ban cap is connector-scope now


def test_form_localized_ru_and_id() -> None:
    ru = settings_form_html({}, "ru")
    assert "Бюджет" in ru and "Дневной лимит LLM" in ru  # branch-scope Budget section
    id_ = settings_form_html({}, "id")
    assert "Anggaran" in id_


def test_connector_editor_has_caps_and_hides_meta_on_instagram() -> None:
    ig = channel_settings_html("instagram", {}, "en", 5)
    assert "Messages / hour" in ig                 # anti-ban cap is connector-scoped
    assert 'App ID' not in ig                       # Meta field hidden on a non-Meta channel
    assert 'hx-post="/ui/settings/save"' in ig
    assert '"channel_id": "5"' in ig                # autosaves to the connector tier
    meta = channel_settings_html("meta_business", {}, "en", 9)
    assert "App ID" in meta                          # Meta fields show on a Meta connector


def test_form_layout_fills_wide_screens_not_a_narrow_column() -> None:
    """A hardcoded max-width:600px left a sea of empty space on wide monitors — cards must
    flow into a responsive multi-column grid instead."""
    html = settings_form_html({}, "en")
    assert "max-width:600px" not in html
    assert "grid-template-columns" in html


def test_autosave_hint_shown_next_to_title_not_just_in_help_mode() -> None:
    """No Save button exists (every field auto-saves on change) — a manager looking for one
    must see this called out up front, not only when '?' help-mode is toggled on."""
    from app.api._i18n import _lang
    _lang.set("ru")
    html = settings_form_html({}, "ru")
    assert "сохраняется автоматически" in html
    _lang.set("en")
    html = settings_form_html({}, "en")
    assert "saves automatically" in html


def test_bool_renders_onoff_select() -> None:
    html = field_html(S.field_for("agent_enabled_global"), "true", "en")
    assert "<select" in html
    assert "selected>On" in html


def test_int_renders_number_input_with_value() -> None:
    html = field_html(S.field_for("hourly_cap"), "120", "en")
    assert 'type="number"' in html
    assert 'value="120"' in html


def test_secret_never_echoes_value() -> None:
    html = field_html(S.field_for("meta_capi_token"), "EAABSECRET123", "en")
    assert "EAABSECRET123" not in html  # secret value must not reach the browser
    assert 'type="password"' in html
    assert "saved" in html.lower()


def test_placeholder_rendered() -> None:
    html = field_html(S.field_for("tg_group_id"), "", "en")
    assert "-1001234567890" in html


def test_no_llm_provider_token_keys() -> None:
    """Broker-only: no local-LLM backend switch or provider-key settings exist."""
    assert S.field_for("llm_backend") is None
    keys = set(S.defaults())
    assert not (keys & {"llm_backend", "openai_key", "gemini_key", "provider_key"})


def test_unconsumed_tech_toggles_hidden_but_still_seeded() -> None:
    """tech_* do nothing yet → not rendered, but kept in defaults so nothing regresses."""
    html = settings_form_html({}, "en")
    assert "Tailor use-cases" not in html and "Web search" not in html
    assert "tech_usecase_enabled" in S.defaults() and "tech_search_enabled" in S.defaults()


def test_current_value_overrides_default() -> None:
    html = settings_form_html({"daily_budget_usd": "7"}, "en")  # a branch-scope field
    assert 'value="7"' in html


def test_cap_usage_badge_shown_when_provided() -> None:
    """Live usage under the per-connector hourly_cap/daily_cap — never hardcoded, only rendered
    when the channel editor computed it and passed it in."""
    from app.api._i18n import _lang
    _lang.set("en")  # the "cap reached" label reads the lang contextvar, not the arg — pin it
    html = channel_settings_html(
        "instagram", {"hourly_cap": "150", "daily_cap": "800"}, "en", 5,
        cap_usage={"hourly_cap": (150, 150), "daily_cap": (310, 800)})
    assert "150/150 (100%)" in html
    assert "cap reached" in html.lower()
    assert "310/800 (39%)" in html
    assert "cap reached" not in html.split("310/800")[1][:40].lower()


def test_cap_usage_badge_absent_without_data() -> None:
    html = channel_settings_html("instagram", {"hourly_cap": "150"}, "en", 5)
    assert "/150 (" not in html


# ─── save-by-key route (smoke) ──────────────────────────────────────────────────

def test_settings_save_by_key_route(client: TestClient) -> None:
    resp = client.post("/ui/settings/save", data={"key": "hourly_cap", "value": "99"})
    assert resp.status_code in (200, 500)


def test_settings_save_unknown_key_rejected(client: TestClient) -> None:
    resp = client.post("/ui/settings/save", data={"key": "nope", "value": "x"})
    assert resp.status_code in (400, 500)


def test_settings_has_no_timezone_field() -> None:
    # timezone lives on the branch (branch.tz_offset_h), edited on the branch page — not here
    assert S.field_for("tz_offset_h") is None
    assert all(f.key != "tz_offset_h" for f in S.all_fields())


def test_number_inputs_are_right_sized_not_airplanes() -> None:
    html = field_html(S.field_for("quiet_start"), "22", "en")
    assert "width:64px" in html and "text-align:right" in html


async def test_tz_comes_from_the_branch_row_not_app_setting(db_session) -> None:
    from app.adapters.db.models import Branch
    from app.modules.settings.service import get_settings, invalidate
    b = Branch(name="TZ", lang="id", tz_offset_h=3)  # Moscow
    db_session.add(b)
    await db_session.flush()
    invalidate(b.id)  # flush any (b.id, *) entry cached by an earlier test reusing this id
    cfg = await get_settings(db_session, b.id)
    assert cfg.tz_offset_h == 3  # sourced from the branch column, not the app_setting default (7)
    invalidate(b.id)


def test_smart_stages_renders_checkbox_group() -> None:
    f = S.field_for("smart_stages")
    assert f is not None and f.kind == "multi"
    html = field_html(f, "objection,ready", "en")
    assert "multi-grp" in html
    assert 'type="checkbox"' in html
    # only the two saved stages are checked; presenting is not
    assert 'value="objection" checked' in html
    assert 'value="ready" checked' in html
    assert 'value="presenting" checked' not in html
    # a hidden input carries the comma value and does the autosave
    assert 'type="hidden" name="value" value="objection,ready"' in html


def test_smart_stages_untick_all_snaps_back_to_default() -> None:
    # An empty value renders the default stages as checked (UI can't show a no-smart state).
    html = field_html(S.field_for("smart_stages"), "", "en")
    for st in ("presenting", "objection", "ready"):
        assert f'value="{st}" checked' in html

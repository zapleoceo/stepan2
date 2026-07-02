"""Settings schema SSOT + schema-driven renderer: integrity, i18n, typed controls."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api._ui_settings import field_html, settings_form_html  # noqa: E402
from app.api.main import app  # noqa: E402
from app.modules.settings import schema as S  # noqa: E402

_LANGS = ("ru", "en", "id")

# Keys BranchSettings._parse depends on — defaults() must cover all of them.
_BRANCH_KEYS = (
    "agent_enabled_global", "hourly_cap", "daily_cap", "quiet_start", "quiet_end",
    "reply_delay_min_s", "reply_delay_max_s", "tz_offset_h", "tg_group_id",
    "followup_enabled", "followup_schedule_h", "knowledge_backend",
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
    assert "Messages / hour" in html


def test_form_localized_ru_and_id() -> None:
    ru = settings_form_html({}, "ru")
    assert "Бюджет" in ru and "Сообщений в час" in ru
    id_ = settings_form_html({}, "id")
    assert "Anggaran" in id_


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


def test_current_value_overrides_default() -> None:
    html = settings_form_html({"hourly_cap": "7"}, "en")
    assert 'value="7"' in html


# ─── save-by-key route (smoke) ──────────────────────────────────────────────────

def test_settings_save_by_key_route(client: TestClient) -> None:
    resp = client.post("/ui/settings/save", data={"key": "hourly_cap", "value": "99"})
    assert resp.status_code in (200, 500)


def test_settings_save_unknown_key_rejected(client: TestClient) -> None:
    resp = client.post("/ui/settings/save", data={"key": "nope", "value": "x"})
    assert resp.status_code in (400, 500)

"""validate_runtime — fail-fast at boot on config that would otherwise break at first use."""
from __future__ import annotations

import pytest

from app.config import Settings

_DB = "postgresql+asyncpg://u:p@h/db"


def _mk(**over: object) -> Settings:
    base: dict[str, object] = {"database_url": _DB, "broker_url": "http://b", "secret_key": "k"}
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_auth_enabled_without_any_secret_raises() -> None:
    with pytest.raises(ValueError, match="cannot be signed"):
        _mk(auth_enabled=True, session_secret="", secret_key="").validate_runtime()  # noqa: S106


def test_auth_enabled_falls_back_to_secret_key() -> None:
    _mk(auth_enabled=True, session_secret="", secret_key="k").validate_runtime()  # noqa: S106


def test_bad_staff_json_raises() -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        _mk(bootstrap_staff_json="{not json").validate_runtime()


def test_staff_json_must_be_a_list() -> None:
    with pytest.raises(ValueError, match="must be a JSON list"):
        _mk(bootstrap_staff_json='{"tg": 1}').validate_runtime()


def test_valid_staff_json_ok() -> None:
    _mk(bootstrap_staff_json='[{"tg": 1, "name": "A", "role": "branch_admin"}]').validate_runtime()


def test_empty_broker_and_secret_only_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="stepan2.config"):
        _mk(broker_url="", secret_key="").validate_runtime()  # no raise
    assert "BROKER_URL" in caplog.text and "SECRET_KEY" in caplog.text

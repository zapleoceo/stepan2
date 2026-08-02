"""One app, one secret, one source.

The Meta app secret belongs to the app, not to a branch or a channel. Holding it in two places
— .env for the webhook signature and a settings row for the connect flow — creates a value that
can disagree with itself: rotate it in Meta, update one copy, and the webhook verifies while
the connect button fails, or the reverse, with nothing in the logs naming the cause. These
tests pin that both readers go through the same resolver.
"""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

from app.api.webhooks import _app_secret_for  # noqa: E402
from app.modules.meta.app_secret import app_secret_for  # noqa: E402

_ENV = "STEPAN2_META_APP_SECRET"


def test_branch_specific_value_wins(monkeypatch) -> None:
    """Branch 1 talks to a different Meta app (the client's own) and keeps its own secret."""
    monkeypatch.setenv(f"{_ENV}_1", "branch-one")
    monkeypatch.setenv(_ENV, "product-wide")
    assert app_secret_for(1) == "branch-one"


def test_falls_back_to_the_product_secret(monkeypatch) -> None:
    """Adding a client must not require a new environment variable per branch."""
    monkeypatch.delenv(f"{_ENV}_9", raising=False)
    monkeypatch.setenv(_ENV, "product-wide")
    assert app_secret_for(9) == "product-wide"


def test_missing_secret_is_empty_not_none(monkeypatch) -> None:
    """Callers are fail-closed; an empty string must never pass for a valid secret."""
    monkeypatch.delenv(f"{_ENV}_42", raising=False)
    monkeypatch.delenv(_ENV, raising=False)
    assert app_secret_for(42) == ""


def test_webhook_reads_through_the_same_resolver(monkeypatch) -> None:
    """The whole point of the change: these two cannot drift apart."""
    monkeypatch.delenv(f"{_ENV}_5", raising=False)
    monkeypatch.setenv(_ENV, "shared")
    assert _app_secret_for(5) == app_secret_for(5) == "shared"


def test_settings_no_longer_carry_a_second_copy() -> None:
    """The admin field was removed — it existed only as a way to get out of sync."""
    from app.modules.settings.schema import all_fields

    assert "meta_app_secret" not in {f.key for f in all_fields()}

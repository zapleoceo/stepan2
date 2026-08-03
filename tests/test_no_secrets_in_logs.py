"""Credentials must not reach the log, and the log is where httpx puts them by default.

Meta's OAuth endpoints take the app secret and the access token as QUERY parameters. httpx
logs each request at INFO as the full URL, so one ordinary connect wrote

    GET https://graph.facebook.com/v21.0/oauth/access_token?...&client_secret=<real secret>...

into the API container log in clear text — observed 2026-08-03 on the first real connect. The
worker had muted httpx years earlier; the API had not, and nothing tested that it had.
"""
from __future__ import annotations

import logging
import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("STEPAN2_SECRET_KEY", Fernet.generate_key().decode())

import pytest  # noqa: E402

from app.api.main import _mute_transport_loggers  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_levels():
    before = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    yield
    for name, level in before.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_transport_loggers_are_muted_to_warning(name: str) -> None:
    logging.getLogger(name).setLevel(logging.INFO)
    _mute_transport_loggers()
    assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_muting_stops_at_warning_and_no_higher(name: str) -> None:
    """Muting must not blind us to real transport failures — WARNING and above still pass.

    Asserted on the logger's own level rather than isEnabledFor(): another test in the suite
    calls logging.disable(), which short-circuits isEnabledFor globally and has nothing to do
    with the setting under test here (see tests/test_channels.py:423)."""
    _mute_transport_loggers()
    level = logging.getLogger(name).level
    assert level > logging.INFO      # an INFO line carrying a secret is dropped
    assert level <= logging.WARNING  # a real failure is not


def test_create_app_applies_it() -> None:
    """The mute has to happen on the path production actually takes, not only when called
    directly from a test."""
    from app.api.main import create_app  # noqa: PLC0415

    logging.getLogger("httpx").setLevel(logging.INFO)
    create_app()
    assert logging.getLogger("httpx").level == logging.WARNING

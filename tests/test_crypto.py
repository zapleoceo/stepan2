"""Fernet encryption + key rotation (MultiFernet): STEPAN2_SECRET_KEY may be a comma-list;
the first key encrypts, all keys decrypt — so rotating keys doesn't brick stored sessions."""
from __future__ import annotations

import os

os.environ.setdefault("STEPAN2_DATABASE_URL", "sqlite+aiosqlite://")

from cryptography.fernet import Fernet  # noqa: E402

_K1 = Fernet.generate_key().decode()
_K2 = Fernet.generate_key().decode()
os.environ.setdefault("STEPAN2_SECRET_KEY", _K1)

import pytest  # noqa: E402

from app.adapters import crypto  # noqa: E402
from app.config import settings  # noqa: E402


def test_roundtrip_single_key(monkeypatch) -> None:
    monkeypatch.setattr(settings(), "secret_key", _K1)
    enc = crypto.encrypt("secret-session")
    assert enc != "secret-session"
    assert crypto.decrypt(enc) == "secret-session"


def test_rotation_old_ciphertext_still_decrypts(monkeypatch) -> None:
    """Encrypt under the old key, then prepend a NEW key (rotation): the old ciphertext must
    still decrypt, and new encrypts use the new (first) key."""
    monkeypatch.setattr(settings(), "secret_key", _K1)
    old_token = crypto.encrypt("legacy-session")
    # rotate: new key first, old key kept for decrypt
    monkeypatch.setattr(settings(), "secret_key", f"{_K2},{_K1}")
    assert crypto.decrypt(old_token) == "legacy-session"   # old ciphertext still readable
    new_token = crypto.encrypt("fresh-session")
    assert crypto.decrypt(new_token) == "fresh-session"
    # once the old key is dropped, the OLD token no longer decrypts (expected)
    monkeypatch.setattr(settings(), "secret_key", _K2)
    with pytest.raises(Exception):  # noqa: B017, PT011 — InvalidToken
        crypto.decrypt(old_token)
    assert crypto.decrypt(new_token) == "fresh-session"    # new one still fine


def test_empty_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(settings(), "secret_key", "")
    with pytest.raises(RuntimeError):
        crypto.encrypt("x")

"""Fernet encryption for channel-session secrets at rest. Key(s) from env only.

STEPAN2_SECRET_KEY may hold ONE key or a comma-separated list to support rotation: the
FIRST key encrypts new data; ALL keys are tried on decrypt (MultiFernet), so a secret
encrypted under an old key still decrypts after you prepend a new key. Rotate by putting
the new key first, keeping the old one until every stored session has been re-saved."""
from __future__ import annotations

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings


def _keys() -> list[str]:
    raw = settings().secret_key
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("STEPAN2_SECRET_KEY must be set (Fernet key, or comma-list)")
    return keys


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in _keys()])


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()

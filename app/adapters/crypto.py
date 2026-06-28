"""Fernet encryption for channel-session secrets at rest. Key from env only."""
from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    key = settings().secret_key
    if not key:
        raise RuntimeError("STEPAN2_SECRET_KEY must be set (Fernet key)")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()

"""Stateless signed session cookie — HMAC-SHA256 over a compact JSON payload.

No server-side session store: the cookie carries the identity claims, signed with the
app secret so it cannot be forged. The auth middleware verifies it on every request,
avoiding a per-request DB hit."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

_ALG = hashlib.sha256


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict, secret: str) -> str:
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret.encode(), body.encode(), _ALG).digest()
    return f"{body}.{_b64e(mac)}"


def verify(token: str, secret: str, max_age_s: int) -> dict | None:
    """Return the payload iff the signature matches and `iat` is within max_age_s."""
    try:
        body, sig = token.split(".", 1)
        expected = hmac.new(secret.encode(), body.encode(), _ALG).digest()
        if not hmac.compare_digest(_b64d(sig), expected):
            return None
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    iat = payload.get("iat", 0)
    if not isinstance(iat, (int, float)) or time.time() - iat > max_age_s:
        return None
    return payload

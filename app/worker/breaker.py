"""A recovery-driven guard for the chat broker — NOT a fixed cooldown.

The broker's own contract is to guarantee a response or report an error, and it rotates keys,
so it can recover at any instant. A blind "skip for N seconds" would leave the fleet idle long
after the broker is ready again — exactly what we don't want. So this is not a timer:

- A reply job that hits a genuine gateway-down error TRIPS the guard (a Redis flag).
- While tripped, the reply fan-out sends ONE canary thread per branch instead of stampeding
  the dead broker with every awaiting thread. Every probe still costs $0 (a failed call bills
  nothing), so we keep gently knocking — respecting the broker, not refusing to call it.
- The moment ANY broker call succeeds, the flag is CLEARED and the whole fleet resumes on the
  next tick. Recovery, not a clock, reopens the gate — a key rotation that fixes the broker in
  5s reopens us in 5s, not 60.

The flag carries a TTL only as a self-heal backstop for the case where no traffic ever runs a
probe to clear it (broker down AND zero awaiting threads); normal reopen is clear()."""
from __future__ import annotations

from typing import Any

_KEY = "broker:down"
# Backstop only: if no probe ever runs to clear the flag, it self-expires so a stuck flag can
# never freeze the fleet forever. Normal reopen is clear() on the first successful broker call.
BACKSTOP_TTL_S = 600


async def trip(redis: Any, ttl_s: int = BACKSTOP_TTL_S) -> None:
    """Mark the broker down. Open until a successful call clears it (or the TTL backstop)."""
    await redis.set(_KEY, "1", ex=int(ttl_s))


async def clear(redis: Any) -> None:
    """The broker answered — reopen the fleet immediately (idempotent; safe if not tripped)."""
    await redis.delete(_KEY)


async def is_open(redis: Any) -> bool:
    """True while the broker is believed down (fan-out throttles to a canary). A missing flag
    or a bad read means closed — never freeze the fleet on an unreadable guard."""
    try:
        return await redis.get(_KEY) is not None
    except Exception:  # noqa: BLE001 — a Redis blip must not block reply dispatch
        return False

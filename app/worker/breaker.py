"""A shared circuit breaker for the chat broker.

When the broker gateway is down (502/503/504/connection), every awaiting thread would
otherwise independently re-run its full reply generation each tick and each hit the same
dead broker — no tokens burned (a failed call bills nothing) but worker slots and latency
wasted on doomed work. One failed reply job TRIPS the breaker; the reply/follow-up fan-out
then skips a tick instead of stampeding a broker that is known to be down.

State lives in Redis so it is shared across every worker job/process. There is no explicit
half-open probe: the trip is time-boxed, so once the cooldown lapses the gate simply reopens
and the next tick's normal traffic re-probes the broker (and re-trips if it is still down)."""
from __future__ import annotations

import time
from typing import Any

_KEY = "broker:down_until"
# How long the fan-out stays skipped after a gateway failure. reply_pending runs ~once a
# minute, so this skips roughly one tick per outage burst and reopens on its own — long
# enough to stop the stampede, short enough to recover within a minute of the broker healing.
COOLDOWN_S = 60.0


async def trip(redis: Any, cooldown_s: float = COOLDOWN_S) -> None:
    """Open the breaker for cooldown_s. The key self-expires so a crashed worker can't leave
    the fleet frozen — the TTL is the cooldown plus a small margin."""
    await redis.set(_KEY, str(time.time() + cooldown_s), ex=int(cooldown_s) + 5)


async def seconds_open(redis: Any) -> float:
    """Seconds the breaker stays open (0.0 = closed). Reads the stored deadline; a missing or
    malformed value is treated as closed (fail-safe: never freeze the fleet on a bad read)."""
    raw = await redis.get(_KEY)
    if raw is None:
        return 0.0
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return max(0.0, float(raw) - time.time())
    except (TypeError, ValueError):
        return 0.0

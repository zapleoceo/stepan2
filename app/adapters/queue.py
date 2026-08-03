"""Enqueue an ARQ job from the API process.

The worker already talks to Redis through the arq context it is given; the web container had
no way to hand work to it at all. A webhook needs exactly that: acknowledge Meta in
milliseconds, do the real work in the worker.

One lazily-built pool for the process lifetime — arq's create_pool opens a connection pool,
and building one per request would spend more time on the socket than on the job.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.config import settings

_pool: Any | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> Any:
    global _pool  # noqa: PLW0603 — module singleton, the project's standard shape
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is None:
            from arq import create_pool  # noqa: PLC0415 — keeps import-time free of Redis
            from arq.connections import RedisSettings  # noqa: PLC0415

            _pool = await create_pool(RedisSettings.from_dsn(settings().redis_url))
    return _pool


async def enqueue(job_name: str, *args: Any, job_id: str | None = None) -> bool:
    """True when the job was queued, False when `job_id` says one is already in flight.

    Raises on a Redis failure by design: the caller (a webhook) must answer with an error so
    the sender retries, rather than acknowledge work that was silently dropped.
    """
    pool = await get_pool()
    job = await pool.enqueue_job(job_name, *args, _job_id=job_id)
    return job is not None


async def reset_pool() -> None:
    """Drop the cached pool (tests, and a reconnect after a Redis restart)."""
    global _pool  # noqa: PLW0603 — see get_pool
    pool, _pool = _pool, None
    if pool is None:
        return
    # redis-py renamed close() → aclose() in 5.x; the pool is pinned by arq, not by us.
    closer = getattr(pool, "aclose", None) or pool.close
    await closer()

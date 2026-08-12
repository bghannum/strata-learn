"""Redis connection for the API process — used both to enqueue arq jobs and to
subscribe to worker progress (Phase 1.5). `ArqRedis` is a thin subclass of
`redis.asyncio.Redis`, so one connection serves both purposes; the arq worker
process gets its own via `ctx["redis"]` and doesn't use this module.

Deliberately NOT a cached module-level singleton — mirrors the NullPool choice
for the DB engine (db/session.py): a connection bound to one event loop breaks
the moment a different loop uses it (e.g. `with TestClient(app) as client:`
spins up a fresh loop per block), and the extra per-request connect cost is
negligible at single-user scale against Redis on localhost/docker-compose,
same reasoning as NullPool there.
"""

from collections.abc import AsyncIterator

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import settings


async def get_redis_pool() -> AsyncIterator[ArqRedis]:
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        yield pool
    finally:
        await pool.aclose()

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings

# NullPool: asyncpg connections are bound to the event loop that created them,
# and this engine is a module-level singleton created once at import time.
# Anything that runs its own event loop distinct from the one serving requests
# (pytest-asyncio fixtures, TestClient's anyio portal, a future worker process)
# would otherwise try to reuse a pooled connection across loops and crash with
# "Future attached to a different loop". NullPool opens a fresh connection per
# checkout instead of pooling, which sidesteps this entirely. At single-user
# scale the extra connect-per-request cost is negligible against Postgres on
# localhost/docker-compose.
engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session

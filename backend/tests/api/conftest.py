"""API tests hit the real Postgres via docker compose (no mocking — consistent
with this project's "boring, debuggable" philosophy). This fixture wipes the
app tables before and after every test so tests don't leak state into each
other; it requires `docker compose up postgres` (or the full stack) running.
"""

import pytest
from sqlalchemy import text

from app.db.session import async_session_factory


async def _clean() -> None:
    async with async_session_factory() as session:
        await session.exec(text("UPDATE repo SET latest_snapshot_id = NULL"))
        await session.exec(text("DELETE FROM codeunit"))
        await session.exec(text("DELETE FROM analysissnapshot"))
        await session.exec(text("DELETE FROM repo"))
        await session.exec(text('DELETE FROM "user"'))
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db():
    await _clean()
    yield
    await _clean()

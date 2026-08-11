import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from sqlalchemy import text

from app.analysis.snapshot import create_pending_snapshot
from app.config import settings
from app.db.models import Repo, SourceType
from app.db.session import async_session_factory


async def _clean_db() -> None:
    async with async_session_factory() as session:
        await session.exec(text("UPDATE repo SET latest_snapshot_id = NULL"))
        await session.exec(text("DELETE FROM codeunit"))
        await session.exec(text("DELETE FROM analysissnapshot"))
        await session.exec(text("DELETE FROM repo"))
        await session.exec(text('DELETE FROM "user"'))
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db() -> AsyncIterator[None]:
    """Hits the real Postgres via docker compose (no mocking — consistent with
    this project's "boring, debuggable" philosophy). Wipes app tables before
    and after every test so tests don't leak state into each other. Lives at
    the root so it applies to every test package, not just tests/api/ — Phase
    1.5's worker tests touch the same tables."""
    await _clean_db()
    yield
    await _clean_db()


@pytest.fixture
async def redis_pool() -> AsyncIterator[ArqRedis]:
    """A real Redis connection (no mocking) for tests exercising arq
    enqueueing or pub/sub directly, independent of the app's own shared pool
    in app/redis_pool.py — tests get their own to avoid coupling to app
    startup/shutdown ordering."""
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield pool
    await pool.aclose()


@pytest.fixture
def pending_repo_factory() -> Callable[[SourceType, str], Awaitable[tuple[UUID, UUID]]]:
    """Creates a Repo + pending AnalysisSnapshot directly via the DB, bypassing
    both the HTTP layer and the real arq enqueue — for tests that drive the
    worker pipeline themselves and don't want an unconsumed job left sitting
    in the queue (no arq worker process runs during the test suite)."""

    async def _make(source_type: SourceType, source_uri: str) -> tuple[UUID, UUID]:
        async with async_session_factory() as session:
            repo = Repo(source_type=source_type, source_uri=source_uri, display_name=source_uri)
            session.add(repo)
            await session.flush()
            snapshot = await create_pending_snapshot(session, repo.id)
            repo.latest_snapshot_id = snapshot.id
            session.add(repo)
            await session.commit()
            return repo.id, snapshot.id

    return _make


@pytest.fixture
def git_fixture_repo(tmp_path: Path) -> Path:
    """A minimal real git repo (one commit, one file) for exercising the
    git-clone path without depending on network access."""
    repo_dir = tmp_path / "fixture-repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@test.com", "-c", "user.name=test", "commit", "-q", "-m", "init"],
        cwd=repo_dir,
        check=True,
    )
    return repo_dir

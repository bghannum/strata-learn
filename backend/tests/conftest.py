import subprocess
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import UUID

import pytest
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.analysis.snapshot import (
    analyze_source,
    complete_snapshot,
    create_pending_snapshot,
)
from app.config import settings
from app.db.models import Repo, SourceType
from app.db.session import async_session_factory

REGISTRATION_SECRET = settings.registration_secret


async def _clean_db() -> None:
    async with async_session_factory() as session:
        await session.exec(text("UPDATE repo SET latest_snapshot_id = NULL"))
        await session.exec(text("DELETE FROM answersubmission"))
        await session.exec(text("DELETE FROM attempt"))
        await session.exec(text("DELETE FROM question"))
        await session.exec(text("DELETE FROM quiz"))
        await session.exec(text("DELETE FROM citation"))
        await session.exec(text("DELETE FROM section"))
        await session.exec(text("DELETE FROM studyguide"))
        await session.exec(text("DELETE FROM codeunit"))
        await session.exec(text("DELETE FROM tradeoffcard"))
        await session.exec(text("DELETE FROM patternclaim"))
        await session.exec(text("DELETE FROM modulesummary"))
        await session.exec(text("DELETE FROM subsystem"))
        await session.exec(text("DELETE FROM analysissnapshot"))
        await session.exec(text("DELETE FROM repo"))
        await session.exec(text("DELETE FROM session"))
        await session.exec(text('DELETE FROM "user"'))
        await session.commit()


def register_test_user(client: TestClient) -> dict:
    """Registers (and, via the response cookie, logs in) a throwaway user on
    the given TestClient — every API endpoint requires a session as of Phase
    4b. TestClient persists cookies across calls on the same instance, so
    callers just do this once before their real requests."""
    email = f"test-{uuid.uuid4()}@example.com"
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "test-password-123", "registration_secret": REGISTRATION_SECRET},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login_as_new_user(client: TestClient, email: str | None = None) -> dict:
    """For tests that need a *second* account to verify cross-user isolation:
    POST /auth/register only ever allows the first account ever created
    (ADR-007's single-tenant design — found via Codex's Phase 4b pre-push
    review), so a second real account can't come from the HTTP endpoint.
    Creates the User + Session directly via the same helpers the real login
    flow uses (app/auth/session.py), then sets the resulting cookie on the
    client exactly as a real login response would — arguably more realistic
    than going through registration anyway, since any actual second account
    in this single-tenant app would come from a different provisioning path,
    not self-registration."""
    from app.api.auth import SESSION_COOKIE_NAME
    from app.auth.security import hash_password
    from app.auth.session import create_session
    from app.db.models import User

    user_email = email or f"test-{uuid.uuid4()}@example.com"
    async with async_session_factory() as session:
        user = User(email=user_email, password_hash=hash_password("test-password-123"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        raw_token = await create_session(session, user.id)

    client.cookies.set(SESSION_COOKIE_NAME, raw_token)
    return {"id": str(user.id), "email": user.email}


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
def pending_repo_factory() -> Callable[..., Awaitable[tuple[UUID, UUID]]]:
    """Creates a Repo + pending AnalysisSnapshot directly via the DB, bypassing
    both the HTTP layer and the real arq enqueue — for tests that drive the
    worker pipeline themselves and don't want an unconsumed job left sitting
    in the queue (no arq worker process runs during the test suite).

    `user_id` is optional (defaults to None, matching Repo.user_id's own
    nullable-until-Phase-4b default) — only tests that then hit the
    ownership-scoped API endpoints (register_test_user + this fixture's
    caller passing that user's id) need to set it."""

    async def _make(source_type: SourceType, source_uri: str, user_id: UUID | None = None) -> tuple[UUID, UUID]:
        async with async_session_factory() as session:
            repo = Repo(source_type=source_type, source_uri=source_uri, display_name=source_uri, user_id=user_id)
            session.add(repo)
            await session.flush()
            snapshot = await create_pending_snapshot(session, repo.id)
            repo.latest_snapshot_id = snapshot.id
            session.add(repo)
            await session.commit()
            return repo.id, snapshot.id

    return _make


@pytest.fixture
def layer_a_ready_factory(
    tmp_path: Path,
) -> Callable[[dict[str, str]], Awaitable[tuple[UUID, UUID, Path]]]:
    """Builds a real Repo + ready AnalysisSnapshot + persisted CodeUnit rows by
    running Layer A for real (analyze_source/complete_snapshot, no LLM
    involved) against caller-supplied file contents — a starting point for
    Layer B tests, which need real CodeUnit rows to chunk/summarize/extract
    trade-offs against. Kept separate from pending_repo_factory rather than
    extending it, since existing callers of that fixture want a bare pending
    snapshot and shouldn't have to opt out of running Layer A."""

    async def _make(files: dict[str, str]) -> tuple[UUID, UUID, Path]:
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        for relative_path, content in files.items():
            path = source_dir / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        analysis = analyze_source(source_dir)

        async with async_session_factory() as session:
            repo = Repo(source_type=SourceType.git_url, source_uri="layer-a-ready-fixture", display_name="fixture")
            session.add(repo)
            await session.flush()
            snapshot = await create_pending_snapshot(session, repo.id)
            repo.latest_snapshot_id = snapshot.id
            session.add(repo)
            await session.commit()

        async with async_session_factory() as session:
            await complete_snapshot(session, snapshot.id, None, analysis)

        return repo.id, snapshot.id, source_dir

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

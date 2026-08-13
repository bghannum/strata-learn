"""Endpoint tests use `with TestClient(app) as client:` deliberately — without
the context manager, each call can spin up a fresh anyio event loop, and our
global asyncpg-backed async engine (created once at import time) then raises
"Future attached to a different loop" on the second call in a test. Entering
the context manager pins one loop for the whole block.

Every endpoint requires a session as of Phase 4b — register_test_user
(tests/conftest.py) registers a throwaway user on the client before the
real request under test, since TestClient persists the resulting cookie
across calls on the same instance.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.db.models import SourceType, StudyGuide
from app.db.session import async_session_factory
from app.main import app
from app.redis_pool import get_redis_pool
from tests.conftest import login_as_new_user, register_test_user


def test_create_repo_from_git_url(git_fixture_repo: Path) -> None:
    # Phase 1.5: POST /repos returns as soon as the job is enqueued, not once
    # indexing finishes — status is "pending" here, not "ready". The full
    # pending -> parsing -> ready path is exercised by
    # tests/api/test_repo_progress_ws.py and tests/worker/test_pipeline.py,
    # which actually drive the worker (nothing does here — no arq worker
    # process runs during the test suite).
    with TestClient(app) as client:
        register_test_user(client)
        response = client.post(
            "/repos",
            data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["source_type"] == "git_url"
        assert body["latest_snapshot_id"] is not None

        snapshot = client.get(f"/repos/{body['id']}/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == "pending"


def test_create_repo_from_zip_upload(tmp_path: Path) -> None:
    import zipfile

    zip_path = tmp_path / "upload.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("hello.py", "def hello():\n    return 'hi'\n")

    with TestClient(app) as client, open(zip_path, "rb") as f:
        register_test_user(client)
        response = client.post(
            "/repos",
            data={"source_type": "zip_upload", "display_name": "zip test"},
            files={"file": ("upload.zip", f, "application/zip")},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "zip_upload"
    assert body["source_uri"] == "upload.zip"
    assert body["display_name"] == "zip test"


def test_create_repo_zip_upload_over_size_limit_returns_422(tmp_path: Path, monkeypatch) -> None:
    # Guards the bounded-read fix: file.read(max_bytes + 1) + a length check,
    # instead of file.read() unconditionally buffering the whole upload
    # before any size check ran.
    monkeypatch.setattr(settings, "zip_upload_max_bytes", 10)
    import zipfile

    zip_path = tmp_path / "upload.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("hello.py", "def hello():\n    return 'hi'\n")

    with TestClient(app) as client, open(zip_path, "rb") as f:
        register_test_user(client)
        response = client.post(
            "/repos",
            data={"source_type": "zip_upload"},
            files={"file": ("upload.zip", f, "application/zip")},
        )
    assert response.status_code == 422


def test_create_repo_enqueue_failure_marks_snapshot_failed(git_fixture_repo: Path) -> None:
    # Simulates Redis going away between the repo/snapshot commit and the
    # enqueue call — a real gap: those rows are already durably committed by
    # that point. Without the fix, they'd be stranded at "pending" forever
    # while the client sees a bare, unexplained 500.
    async def broken_redis_pool() -> AsyncIterator[object]:
        class _BrokenRedis:
            async def set(self, *args, **kwargs):
                raise ConnectionError("simulated redis outage")

            async def enqueue_job(self, *args, **kwargs):
                raise ConnectionError("simulated redis outage")

        yield _BrokenRedis()

    app.dependency_overrides[get_redis_pool] = broken_redis_pool
    try:
        with TestClient(app) as client:
            register_test_user(client)
            response = client.post(
                "/repos",
                data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
            )
            assert response.status_code == 503

            listed = client.get("/repos").json()
            assert len(listed) == 1  # committed despite the 503 — exactly what's being guarded against
            snapshot = client.get(f"/repos/{listed[0]['id']}/snapshot").json()
            assert snapshot["status"] == "failed"
    finally:
        app.dependency_overrides.pop(get_redis_pool, None)


def test_create_repo_redis_unreachable_at_dependency_time_returns_503(git_fixture_repo: Path) -> None:
    # Distinct from the enqueue-failure case above: here Redis fails while
    # get_redis_pool's own create_pool() call is resolving, before
    # create_repo's body runs at all — so nothing's committed to clean up,
    # and api/repos.py's own try/except never even gets a chance to fire.
    # This is main.py's app-level RedisError handler's job specifically.
    async def failing_redis_pool() -> AsyncIterator[object]:
        raise RedisConnectionError("simulated: redis completely unreachable")
        yield  # pragma: no cover - unreachable; keeps this a generator so it matches get_redis_pool's shape

    app.dependency_overrides[get_redis_pool] = failing_redis_pool
    try:
        with TestClient(app) as client:
            register_test_user(client)
            response = client.post(
                "/repos",
                data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
            )
            assert response.status_code == 503
            assert client.get("/repos").json() == []  # nothing committed — dependency failed first
    finally:
        app.dependency_overrides.pop(get_redis_pool, None)


def test_create_repo_bad_git_url_returns_422() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.post(
            "/repos", data={"source_type": "git_url", "git_url": "file:///definitely/does/not/exist"}
        )
    assert response.status_code == 422


def test_create_repo_missing_git_url_returns_422() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.post("/repos", data={"source_type": "git_url"})
    assert response.status_code == 422


def test_create_repo_missing_file_returns_422() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.post("/repos", data={"source_type": "zip_upload"})
    assert response.status_code == 422


def test_list_and_get_repo(git_fixture_repo: Path) -> None:
    with TestClient(app) as client:
        register_test_user(client)
        created = client.post(
            "/repos",
            data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
        ).json()

        listed = client.get("/repos")
        assert listed.status_code == 200
        assert any(r["id"] == created["id"] for r in listed.json())

        detail = client.get(f"/repos/{created['id']}")
        assert detail.status_code == 200
        assert detail.json()["id"] == created["id"]


async def test_list_repos_only_returns_the_current_users_own(git_fixture_repo: Path) -> None:
    with TestClient(app) as client_a:
        register_test_user(client_a)
        created = client_a.post(
            "/repos",
            data={"source_type": "git_url", "git_url": git_fixture_repo.as_uri()},
        ).json()

    # A second account can't come from POST /auth/register (only the first
    # account ever created is allowed — ADR-007's single-tenant design), so
    # this uses login_as_new_user's DB-level bypass instead.
    with TestClient(app) as client_b:
        await login_as_new_user(client_b)
        listed_b = client_b.get("/repos").json()
        assert listed_b == []  # a second account sees none of the first account's repos

        # Same "404, not 403" reasoning as the ownership checks themselves —
        # a real repo_id belonging to someone else isn't reachable either.
        assert client_b.get(f"/repos/{created['id']}").status_code == 404
        assert client_b.get(f"/repos/{created['id']}/snapshot").status_code == 404


def test_get_repo_not_found_returns_404() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/repos/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


async def test_get_repo_study_guide_redirects_to_canonical_resource(pending_repo_factory) -> None:
    # StudyGuide.id is generated inside the worker and never surfaces
    # through create_repo's response or the snapshot endpoint — this is the
    # only way an API client can discover it (found via Codex's Phase 3
    # pre-push review).
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        async with async_session_factory() as session:
            guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
            session.add(guide)
            await session.commit()
            guide_id = guide.id

        response = client.get(f"/repos/{repo_id}/study-guide", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == f"/study-guides/{guide_id}"


async def test_get_repo_study_guide_404_before_guide_exists(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, _snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        response = client.get(f"/repos/{repo_id}/study-guide")

    assert response.status_code == 404


def test_get_repo_study_guide_404_for_unknown_repo() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/repos/00000000-0000-0000-0000-000000000000/study-guide")
    assert response.status_code == 404


def test_get_snapshot_for_repo_without_one_is_unreachable() -> None:
    # every repo created via POST /repos gets a pending snapshot synchronously
    # (only the indexing itself is async as of Phase 1.5, D13) — there's no
    # code path that creates a repo without one, so 404 is only reachable via
    # a nonexistent repo id
    with TestClient(app) as client:
        register_test_user(client)
        response = client.get("/repos/00000000-0000-0000-0000-000000000000/snapshot")
    assert response.status_code == 404

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
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.db.models import (
    AnalysisSnapshot,
    AnswerSubmission,
    Attempt,
    AttemptStatus,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    Repo,
    SnapshotStatus,
    SourceType,
    StudyGuide,
    Subsystem,
)
from app.db.session import async_session_factory
from app.main import app
from app.redis_pool import get_redis_pool
from tests.conftest import login_as_new_user, register_test_user


async def _fail_snapshot(snapshot_id: UUID) -> None:
    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
        snapshot.status = SnapshotStatus.failed
        session.add(snapshot)
        await session.commit()


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


async def test_reindex_failed_git_repo_creates_new_pending_snapshot(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, old_snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        await _fail_snapshot(old_snapshot_id)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 202
    body = response.json()
    assert body["latest_snapshot_id"] != str(old_snapshot_id)

    async with async_session_factory() as session:
        new_snapshot = await session.get(AnalysisSnapshot, UUID(body["latest_snapshot_id"]))
        assert new_snapshot is not None
        assert new_snapshot.status == SnapshotStatus.pending


async def test_reindex_failed_zip_repo_copies_bytes_to_new_snapshot_key(pending_repo_factory, redis_pool) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, old_snapshot_id = await pending_repo_factory(
            SourceType.zip_upload, "upload.zip", user_id=UUID(user["id"])
        )
        await _fail_snapshot(old_snapshot_id)
        await redis_pool.set(f"zip-upload:{old_snapshot_id}", b"fake zip bytes", ex=3600)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 202
    new_snapshot_id = response.json()["latest_snapshot_id"]
    copied = await redis_pool.get(f"zip-upload:{new_snapshot_id}")
    assert copied == b"fake zip bytes"


async def test_reindex_failed_zip_repo_with_expired_bytes_returns_410(pending_repo_factory) -> None:
    # No zip-upload:{old_snapshot_id} key set — simulates the 24h TTL having
    # expired before the person came back to retry.
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, old_snapshot_id = await pending_repo_factory(
            SourceType.zip_upload, "upload.zip", user_id=UUID(user["id"])
        )
        await _fail_snapshot(old_snapshot_id)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 410
    # Nothing new committed — the repo's latest_snapshot_id is unchanged.
    async with async_session_factory() as session:
        repo = await session.get(Repo, repo_id)
        assert repo is not None
        assert repo.latest_snapshot_id == old_snapshot_id


async def test_reindex_non_failed_snapshot_returns_409(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, _snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        # Still "pending" — never marked failed.
        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 409


def test_reindex_unknown_repo_returns_404() -> None:
    with TestClient(app) as client:
        register_test_user(client)
        response = client.post("/repos/00000000-0000-0000-0000-000000000000/reindex")
    assert response.status_code == 404


async def test_reindex_another_users_repo_returns_404(pending_repo_factory) -> None:
    with TestClient(app) as client:
        owner = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(owner["id"])
        )
        await _fail_snapshot(snapshot_id)

        await login_as_new_user(client)
        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 404


# --- #62: staleness detection ---


async def _set_snapshot_commit(snapshot_id: UUID, commit_hash: str) -> None:
    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
        snapshot.commit_hash = commit_hash
        session.add(snapshot)
        await session.commit()


async def _git_repo(client: TestClient, pending_repo_factory) -> tuple[UUID, UUID]:
    user = register_test_user(client)
    return await pending_repo_factory(
        SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
    )


async def test_update_status_is_unknown_before_any_check(pending_repo_factory) -> None:
    with TestClient(app) as client:
        repo_id, _snapshot_id = await _git_repo(client, pending_repo_factory)
        body = client.get(f"/repos/{repo_id}/update-status").json()

    assert body["status"] == "unknown"
    assert body["reason"] == "never_checked"
    assert body["checked_at"] is None


async def test_update_status_does_no_network_io(pending_repo_factory, monkeypatch) -> None:
    # The whole reason checking is an explicit action (#62): a git ls-remote on
    # a page-load path would make every repo view wait on a third-party host
    # that can hang.
    def _boom(*args, **kwargs):
        raise AssertionError("update-status must not reach the remote")

    monkeypatch.setattr("app.api.repos.get_remote_head_commit", _boom)

    with TestClient(app) as client:
        repo_id, _snapshot_id = await _git_repo(client, pending_repo_factory)
        assert client.get(f"/repos/{repo_id}/update-status").status_code == 200


async def test_check_updates_reports_stale_when_the_remote_moved(pending_repo_factory, monkeypatch) -> None:
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "b" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        body = client.post(f"/repos/{repo_id}/check-updates").json()

    assert body["status"] == "stale"
    assert body["indexed_commit"] == "a" * 40
    assert body["remote_commit"] == "b" * 40
    assert body["checked_at"] is not None


async def test_check_updates_reports_up_to_date(pending_repo_factory, monkeypatch) -> None:
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "a" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        assert client.post(f"/repos/{repo_id}/check-updates").json()["status"] == "up_to_date"


async def test_check_updates_result_persists_for_the_next_read(pending_repo_factory, monkeypatch) -> None:
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "b" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        client.post(f"/repos/{repo_id}/check-updates")
        body = client.get(f"/repos/{repo_id}/update-status").json()

    assert body["status"] == "stale"
    assert body["remote_commit"] == "b" * 40


async def test_unreachable_remote_is_unknown_not_an_error(pending_repo_factory, monkeypatch) -> None:
    # The repo and its guide are both still fine — a 5xx would misrepresent a
    # network hiccup as a broken request.
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: None)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        response = client.post(f"/repos/{repo_id}/check-updates")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["reason"] == "remote_unreachable"


async def test_staleness_is_recomputed_not_stored(pending_repo_factory, monkeypatch) -> None:
    # A stored verdict would go silently wrong the moment a reindex changes the
    # indexed commit without anyone re-running the check.
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "b" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        assert client.post(f"/repos/{repo_id}/check-updates").json()["status"] == "stale"

        # the repo is re-indexed up to the remote's commit; no new check is run
        await _set_snapshot_commit(snapshot_id, "b" * 40)
        assert client.get(f"/repos/{repo_id}/update-status").json()["status"] == "up_to_date"


async def test_zip_upload_cannot_be_checked(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, _snapshot_id = await pending_repo_factory(
            SourceType.zip_upload, "upload.zip", user_id=UUID(user["id"])
        )
        assert client.get(f"/repos/{repo_id}/update-status").json()["reason"] == "zip_upload"
        assert client.post(f"/repos/{repo_id}/check-updates").status_code == 409


async def test_update_status_is_scoped_to_its_owner(pending_repo_factory) -> None:
    with TestClient(app) as owner:
        repo_id, _snapshot_id = await _git_repo(owner, pending_repo_factory)

    with TestClient(app) as other:
        # A second account can't come from POST /auth/register (ADR-007's
        # single-tenant design) — same DB-level bypass the other cross-user
        # isolation tests here use.
        await login_as_new_user(other)
        assert other.get(f"/repos/{repo_id}/update-status").status_code == 404
        assert other.post(f"/repos/{repo_id}/check-updates").status_code == 404


# --- #64: mastery tracking ---


async def _completed_attempt(
    repo_id: UUID,
    snapshot_id: UUID,
    user_id: UUID,
    answers: list[tuple[str | None, float]],
    version: int = 1,
    minutes: int = 0,
) -> UUID:
    """A study guide, a quiz with one question per answer, and a completed
    attempt over it — the shape mastery aggregates from. Returns the attempt id.

    Attempt.score mirrors what api/attempts.py's completion actually writes (the
    mean over every question), so the quiz-history tests below read a realistic
    row rather than one only mastery's own recomputation would accept.
    """
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=version)
        session.add(guide)
        await session.flush()

        quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
        session.add(quiz)
        await session.flush()

        questions = []
        for order, (subsystem_key, _score) in enumerate(answers):
            question = Question(
                quiz_id=quiz.id, question_type=QuestionType.mcq, order=order, prompt="q",
                choices=["a", "b"], correct_index=0, file_path="a.py", line_start=1, line_end=2,
                subsystem_key=subsystem_key, prompt_version="v2", model="fake",
            )
            session.add(question)
            questions.append(question)
        await session.flush()

        attempt = Attempt(
            quiz_id=quiz.id, user_id=user_id, status=AttemptStatus.completed,
            completed_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=minutes),
            score=sum(score for _key, score in answers) / len(answers) if answers else 0.0,
        )
        session.add(attempt)
        await session.flush()
        for question, (_key, score) in zip(questions, answers, strict=True):
            session.add(
                AnswerSubmission(attempt_id=attempt.id, question_id=question.id, selected_index=0, score=score)
            )
        await session.commit()
        return attempt.id


async def test_mastery_is_empty_before_any_quiz(pending_repo_factory) -> None:
    with TestClient(app) as client:
        repo_id, _snapshot_id = await _git_repo(client, pending_repo_factory)
        body = client.get(f"/repos/{repo_id}/mastery").json()

    assert body == {"completed_attempts": 0, "buckets": []}


async def test_mastery_aggregates_per_subsystem_and_names_them(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        async with async_session_factory() as session:
            session.add(
                Subsystem(
                    snapshot_id=snapshot_id, key="app/api", name="HTTP API", role="r",
                    file_paths=["a.py"], depth=0, order=0, prompt_version="v1", model="fake",
                )
            )
            await session.commit()

        await _completed_attempt(repo_id, snapshot_id, UUID(user["id"]), [("app/api", 1.0), ("app/db", 0.0)])
        body = client.get(f"/repos/{repo_id}/mastery").json()

    assert body["completed_attempts"] == 1
    by_key = {b["subsystem_key"]: b for b in body["buckets"]}
    assert by_key["app/api"]["name"] == "HTTP API"
    assert by_key["app/api"]["average_score"] == 1.0
    # weakest first — this view exists to answer "what should I study next"
    assert body["buckets"][0]["subsystem_key"] == "app/db"


async def test_mastery_spans_study_guide_versions(pending_repo_factory) -> None:
    # The whole point of #61's stable key: a re-index replaces every Section,
    # Question, and Citation row, so two quizzes generated from two different
    # study-guide versions must still aggregate into one history.
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        await _completed_attempt(repo_id, snapshot_id, UUID(user["id"]), [("app/api", 0.0)], version=1, minutes=0)
        await _completed_attempt(
            repo_id, snapshot_id, UUID(user["id"]), [("app/api", 1.0)], version=2, minutes=60
        )

        body = client.get(f"/repos/{repo_id}/mastery").json()

    bucket = body["buckets"][0]
    assert bucket["subsystem_key"] == "app/api"
    assert bucket["attempts"] == 2
    assert bucket["average_score"] == 0.5
    assert [p["average_score"] for p in bucket["history"]] == [0.0, 1.0]


async def test_in_progress_attempts_do_not_count(pending_repo_factory) -> None:
    # An abandoned attempt is not evidence of anything, and its partial answers
    # would drag down an average for a quiz never actually finished.
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        async with async_session_factory() as session:
            guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
            session.add(guide)
            await session.flush()
            quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
            session.add(quiz)
            await session.flush()
            question = Question(
                quiz_id=quiz.id, question_type=QuestionType.mcq, order=0, prompt="q", choices=["a"],
                correct_index=0, file_path="a.py", line_start=1, line_end=2,
                subsystem_key="app/api", prompt_version="v2", model="fake",
            )
            session.add(question)
            attempt = Attempt(quiz_id=quiz.id, user_id=UUID(user["id"]), status=AttemptStatus.in_progress)
            session.add(attempt)
            await session.flush()
            session.add(
                AnswerSubmission(attempt_id=attempt.id, question_id=question.id, selected_index=0, score=0.0)
            )
            await session.commit()

        body = client.get(f"/repos/{repo_id}/mastery").json()

    assert body == {"completed_attempts": 0, "buckets": []}


async def test_mastery_is_scoped_to_its_owner(pending_repo_factory) -> None:
    with TestClient(app) as owner:
        repo_id, _snapshot_id = await _git_repo(owner, pending_repo_factory)

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert other.get(f"/repos/{repo_id}/mastery").status_code == 404


# --- Quiz history (RepoDetail.tsx's per-sitting record, not mastery's per-topic one) ---


async def test_quiz_history_is_empty_before_any_attempt(pending_repo_factory) -> None:
    with TestClient(app) as client:
        repo_id, _snapshot_id = await _git_repo(client, pending_repo_factory)
        response = client.get(f"/repos/{repo_id}/attempts")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


async def test_quiz_history_lists_completed_attempts_newest_first(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        older = await _completed_attempt(
            repo_id, snapshot_id, UUID(user["id"]), [("app/api", 1.0), ("app/db", 0.0)], version=1, minutes=0
        )
        newer = await _completed_attempt(
            repo_id, snapshot_id, UUID(user["id"]), [("app/api", 1.0)], version=2, minutes=60
        )

        body = client.get(f"/repos/{repo_id}/attempts").json()

    rows = body["items"]
    assert [row["id"] for row in rows] == [str(newer), str(older)]
    assert body["total"] == 2
    assert rows[0]["score"] == 1.0
    assert rows[0]["question_count"] == 1
    assert rows[1]["score"] == 0.5
    # The count is the quiz's, not the number of answers submitted — a quiz
    # finished early still reads "2 questions".
    assert rows[1]["question_count"] == 2


async def test_quiz_history_excludes_in_progress_attempts(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        async with async_session_factory() as session:
            guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
            session.add(guide)
            await session.flush()
            quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
            session.add(quiz)
            await session.flush()
            session.add(Attempt(quiz_id=quiz.id, user_id=UUID(user["id"]), status=AttemptStatus.in_progress))
            await session.commit()

        assert client.get(f"/repos/{repo_id}/attempts").json() == {"items": [], "total": 0}


async def test_quiz_history_is_scoped_to_its_owner(pending_repo_factory) -> None:
    with TestClient(app) as owner:
        repo_id, _snapshot_id = await _git_repo(owner, pending_repo_factory)

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert other.get(f"/repos/{repo_id}/attempts").status_code == 404


async def test_quiz_history_is_bounded_but_reports_the_full_count(pending_repo_factory) -> None:
    # #75: retakes are unlimited, so an unbounded response grows forever. The
    # page has to stay bounded *and* be able to say how much it isn't showing.
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        for minutes in range(12):
            await _completed_attempt(
                repo_id, snapshot_id, UUID(user["id"]), [("app/api", 1.0)], version=minutes + 1, minutes=minutes
            )

        default_page = client.get(f"/repos/{repo_id}/attempts").json()
        larger_page = client.get(f"/repos/{repo_id}/attempts", params={"limit": 50}).json()

    assert len(default_page["items"]) == 10
    assert default_page["total"] == 12
    # Newest first, so the page that gets truncated is the old end of the
    # history — the sittings nobody scrolls to.
    assert default_page["items"][0]["completed_at"] > default_page["items"][-1]["completed_at"]
    assert len(larger_page["items"]) == 12
    assert larger_page["total"] == 12


async def test_quiz_history_limit_is_capped(pending_repo_factory) -> None:
    # An unbounded escape hatch would just move #75's problem behind a query
    # parameter, so the ceiling is enforced rather than advisory.
    with TestClient(app) as client:
        repo_id, _snapshot_id = await _git_repo(client, pending_repo_factory)

        assert client.get(f"/repos/{repo_id}/attempts", params={"limit": 101}).status_code == 422
        assert client.get(f"/repos/{repo_id}/attempts", params={"limit": 0}).status_code == 422


# --- #72: the version list the architectural-diff picker chooses from ---


async def test_study_guide_versions_are_listed_newest_first_with_commits(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=UUID(user["id"])
        )
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        async with async_session_factory() as session:
            second_snapshot = AnalysisSnapshot(
                repo_id=repo_id, commit_hash="b" * 40, status=SnapshotStatus.ready
            )
            session.add(second_snapshot)
            await session.flush()
            session.add(StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1))
            session.add(StudyGuide(repo_id=repo_id, snapshot_id=second_snapshot.id, version=2))
            await session.commit()

        body = client.get(f"/repos/{repo_id}/study-guides").json()

    # Newest first, and by version — the same ordering the diff endpoint uses to
    # decide which side is "before", so the picker can't label a diff backwards.
    assert [row["version"] for row in body] == [2, 1]
    assert body[0]["commit_hash"] == "b" * 40
    assert body[1]["commit_hash"] == "a" * 40


async def test_study_guide_versions_are_empty_before_generation(pending_repo_factory) -> None:
    with TestClient(app) as client:
        repo_id, _snapshot_id = await _git_repo(client, pending_repo_factory)
        response = client.get(f"/repos/{repo_id}/study-guides")

    assert response.status_code == 200
    assert response.json() == []


async def test_study_guide_versions_are_scoped_to_their_owner(pending_repo_factory) -> None:
    with TestClient(app) as owner:
        repo_id, _snapshot_id = await _git_repo(owner, pending_repo_factory)

    with TestClient(app) as other:
        await login_as_new_user(other)
        assert other.get(f"/repos/{repo_id}/study-guides").status_code == 404


# --- #73: re-indexing a healthy repo ---


async def _set_snapshot_status(snapshot_id: UUID, status: SnapshotStatus) -> None:
    async with async_session_factory() as session:
        snapshot = await session.get(AnalysisSnapshot, snapshot_id)
        assert snapshot is not None
        snapshot.status = status
        session.add(snapshot)
        await session.commit()


async def test_ready_repo_reindexes_when_the_remote_moved(pending_repo_factory, monkeypatch) -> None:
    # The middle step of the versioning story: staleness detection could
    # previously say "new commits on the remote" and offer no way to act.
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "b" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.ready)
        await _set_snapshot_commit(snapshot_id, "a" * 40)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 202
    assert response.json()["latest_snapshot_id"] != str(snapshot_id)


async def test_ready_repo_at_the_same_commit_is_refused(pending_repo_factory, monkeypatch) -> None:
    # Re-running the pipeline spends the most expensive part of it to
    # regenerate what already exists — reasonable to ask for deliberately, bad
    # to do by double-click.
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "a" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.ready)
        await _set_snapshot_commit(snapshot_id, "a" * 40)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 409
    assert "force" in response.json()["detail"]


async def test_force_reindexes_an_unchanged_repo(pending_repo_factory, monkeypatch) -> None:
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "a" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.ready)
        await _set_snapshot_commit(snapshot_id, "a" * 40)

        response = client.post(f"/repos/{repo_id}/reindex", json={"force": True})

    assert response.status_code == 202


async def test_an_unreachable_remote_does_not_block_a_reindex(pending_repo_factory, monkeypatch) -> None:
    # An unreachable remote can't prove the repo is unchanged; refusing on a
    # network hiccup would block a legitimate action for the wrong reason.
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: None)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.ready)
        await _set_snapshot_commit(snapshot_id, "a" * 40)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 202


async def test_the_freshness_check_is_recorded(pending_repo_factory, monkeypatch) -> None:
    # The request that spends the money does its own check rather than trusting
    # a cached answer, and keeps update-status honest by storing the result.
    monkeypatch.setattr("app.api.repos.get_remote_head_commit", lambda *a, **k: "b" * 40)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.ready)
        await _set_snapshot_commit(snapshot_id, "a" * 40)
        client.post(f"/repos/{repo_id}/reindex")

        body = client.get(f"/repos/{repo_id}/update-status").json()

    assert body["remote_commit"] == "b" * 40
    assert body["checked_at"] is not None


async def test_in_flight_indexing_blocks_a_reindex(pending_repo_factory) -> None:
    # A second job would race the first for the same latest_snapshot_id and
    # double the billed work.
    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.analyzing)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 409
    assert "already in progress" in response.json()["detail"]


async def test_failed_repo_still_retries_without_a_remote_check(pending_repo_factory, monkeypatch) -> None:
    # #26's retry semantics are unchanged: a failed run is always eligible, and
    # must not be gated behind "has the remote moved" — the point is to retry
    # the same commit.
    def _boom(*args, **kwargs):
        raise AssertionError("a failed retry must not check the remote")

    monkeypatch.setattr("app.api.repos.get_remote_head_commit", _boom)

    with TestClient(app) as client:
        repo_id, snapshot_id = await _git_repo(client, pending_repo_factory)
        await _set_snapshot_status(snapshot_id, SnapshotStatus.failed)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 202


async def test_ready_zip_upload_requires_force(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.zip_upload, "upload.zip", user_id=UUID(user["id"])
        )
        await _set_snapshot_status(snapshot_id, SnapshotStatus.ready)

        response = client.post(f"/repos/{repo_id}/reindex")

    assert response.status_code == 409
    assert "no remote" in response.json()["detail"]

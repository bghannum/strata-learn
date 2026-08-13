"""See tests/api/test_repos.py's module docstring for why every test here
uses `with TestClient(app) as client:` and register_test_user."""

import uuid
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from app.db.models import Question, QuestionType, Quiz, QuizStatus, SourceType, StudyGuide
from app.db.session import async_session_factory
from app.main import app
from app.redis_pool import get_redis_pool
from tests.conftest import register_test_user


class _RecordingRedis:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    async def enqueue_job(self, name, **kwargs):
        self.enqueued.append({"name": name, **kwargs})


async def _override_redis() -> AsyncIterator[_RecordingRedis]:
    yield _RecordingRedis()


async def _make_ready_study_guide(repo_id, snapshot_id) -> uuid.UUID:
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.commit()
        await session.refresh(guide)
        return guide.id


async def test_generate_quiz_returns_409_without_a_study_guide(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, _snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
        )
        response = client.post(f"/quizzes/{repo_id}/generate")
    assert response.status_code == 409


async def test_generate_quiz_creates_pending_quiz_and_enqueues_job(pending_repo_factory) -> None:
    app.dependency_overrides[get_redis_pool] = _override_redis
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            repo_id, snapshot_id = await pending_repo_factory(
                SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
            )
            guide_id = await _make_ready_study_guide(repo_id, snapshot_id)

            response = client.post(f"/quizzes/{repo_id}/generate")

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "generating"
        assert body["study_guide_id"] == str(guide_id)
        assert body["questions"] == []
    finally:
        app.dependency_overrides.pop(get_redis_pool, None)


async def test_generate_quiz_404_for_another_users_repo(pending_repo_factory) -> None:
    with TestClient(app) as client_a:
        user_a = register_test_user(client_a)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user_a["id"])
        )
        await _make_ready_study_guide(repo_id, snapshot_id)

    from tests.conftest import login_as_new_user

    with TestClient(app) as client_b:
        await login_as_new_user(client_b)
        response = client_b.post(f"/quizzes/{repo_id}/generate")
    assert response.status_code == 404


async def test_get_quiz_hides_answer_key_while_ready(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
        )
        guide_id = await _make_ready_study_guide(repo_id, snapshot_id)

        async with async_session_factory() as session:
            quiz = Quiz(repo_id=repo_id, study_guide_id=guide_id, status=QuizStatus.ready)
            session.add(quiz)
            await session.flush()
            question = Question(
                quiz_id=quiz.id, question_type=QuestionType.mcq, order=0, prompt="What does this do?",
                choices=["a", "b"], correct_index=1, explanation="secret reasoning",
                file_path="app.py", line_start=1, line_end=2, prompt_version="v1", model="fake-model",
            )
            session.add(question)
            await session.commit()
            quiz_id = quiz.id

        response = client.get(f"/quizzes/{quiz_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert len(body["questions"]) == 1
    returned_question = body["questions"][0]
    assert returned_question["prompt"] == "What does this do?"
    assert returned_question["choices"] == ["a", "b"]
    # no answer key at all in the JSON shape — not just null-valued
    assert "correct_index" not in returned_question
    assert "explanation" not in returned_question


async def test_get_quiz_returns_no_questions_while_generating(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
        )
        guide_id = await _make_ready_study_guide(repo_id, snapshot_id)

        async with async_session_factory() as session:
            quiz = Quiz(repo_id=repo_id, study_guide_id=guide_id, status=QuizStatus.generating)
            session.add(quiz)
            await session.commit()
            quiz_id = quiz.id

        response = client.get(f"/quizzes/{quiz_id}")

    assert response.status_code == 200
    assert response.json()["questions"] == []

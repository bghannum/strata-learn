"""See tests/api/test_repos.py's module docstring for the `with TestClient`
pattern this file follows throughout."""

import uuid

from fastapi.testclient import TestClient

from app.api.attempts import get_llm_provider
from app.db.models import (
    FillBlankMode,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    SourceType,
    StudyGuide,
)
from app.db.session import async_session_factory
from app.main import app
from app.quizzing.grading.fill_blank_grader import FillBlankGradeOutput
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from tests.conftest import login_as_new_user, register_test_user


async def _make_quiz_with_questions(pending_repo_factory, user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git", user_id=user_id)
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.flush()
        quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
        session.add(quiz)
        await session.flush()

        mcq = Question(
            quiz_id=quiz.id, question_type=QuestionType.mcq, order=0, prompt="q1",
            choices=["a", "b"], correct_index=1, explanation="b is right",
            file_path="app.py", line_start=1, line_end=2, prompt_version="v1", model="fake-model",
        )
        fill_blank = Question(
            quiz_id=quiz.id, question_type=QuestionType.fill_blank, order=1, prompt="uses ___",
            fill_blank_mode=FillBlankMode.code, correct_answer="arq", acceptable_alternatives=[],
            file_path="worker.py", line_start=3, line_end=4, prompt_version="v1", model="fake-model",
        )
        session.add(mcq)
        session.add(fill_blank)
        await session.commit()
        return quiz.id, mcq.id, fill_blank.id


async def test_create_attempt_404_for_another_users_quiz(pending_repo_factory) -> None:
    with TestClient(app) as client_a:
        user_a = register_test_user(client_a)
        quiz_id, _mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user_a["id"]))

    with TestClient(app) as client_b:
        await login_as_new_user(client_b)
        response = client_b.post("/attempts", json={"quiz_id": str(quiz_id)})
    assert response.status_code == 404


async def test_submit_mcq_answer_grades_immediately(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))

        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        response = client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 1.0
    assert body["feedback"] == "b is right"
    assert body["correct_index"] == 1


async def test_submit_fill_blank_exact_match_never_calls_llm(pending_repo_factory) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider([])
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, _mcq_id, fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

            response = client.patch(f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "arq"})

        assert response.status_code == 200
        assert response.json()["score"] == 1.0
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_submit_fill_blank_concept_mode_miss_uses_llm_judge(pending_repo_factory) -> None:
    fake_llm = FakeLLMProvider(
        [
            LLMResponse(
                text="", model="fake-model", stop_reason="end_turn", usage={},
                parsed=FillBlankGradeOutput(score=0.5, feedback="close"),
            )
        ]
    )
    app.dependency_overrides[get_llm_provider] = lambda: fake_llm
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            repo_id, snapshot_id = await pending_repo_factory(
                SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
            )
            async with async_session_factory() as session:
                guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
                session.add(guide)
                await session.flush()
                quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
                session.add(quiz)
                await session.flush()
                fb = Question(
                    quiz_id=quiz.id, question_type=QuestionType.fill_blank, order=0, prompt="uses ___ for jobs",
                    fill_blank_mode=FillBlankMode.concept, correct_answer="arq", acceptable_alternatives=[],
                    file_path="worker.py", line_start=1, line_end=2, prompt_version="v1", model="fake-model",
                )
                session.add(fb)
                await session.commit()
                quiz_id, fb_id = quiz.id, fb.id

            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            response = client.patch(f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "some queueing thing"})

        assert response.status_code == 200
        assert response.json()["score"] == 0.5
        assert len(fake_llm.calls) == 1
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_submit_answer_409_once_attempt_is_completed(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})
        client.post(f"/attempts/{attempt['id']}/complete")

        response = client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 0})

    assert response.status_code == 409


async def test_complete_attempt_averages_over_all_questions_unanswered_counts_zero(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        # Only answer the mcq (correctly) — leave fill_blank unanswered.
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})
        response = client.post(f"/attempts/{attempt['id']}/complete")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["score"] == 0.5  # 1.0 + 0.0 (unanswered) over 2 questions
    unanswered = next(q for q in body["questions"] if q["question_id"] != mcq_id.__str__())
    assert unanswered["score"] is None


async def test_complete_attempt_422_with_no_answers(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        response = client.post(f"/attempts/{attempt['id']}/complete")

    assert response.status_code == 422


async def test_get_attempt_reflects_completed_state(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})
        client.post(f"/attempts/{attempt['id']}/complete")

        response = client.get(f"/attempts/{attempt['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["score"] == 0.5

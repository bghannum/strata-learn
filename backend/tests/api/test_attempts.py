"""See tests/api/test_repos.py's module docstring for the `with TestClient`
pattern this file follows throughout."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.api.attempts import MAX_ANSWER_TEXT_CHARS, get_llm_provider
from app.config import settings
from app.db.models import (
    AnswerSubmission,
    Attempt,
    Citation,
    FillBlankMode,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    Section,
    SectionType,
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


async def test_submit_mcq_answer_grades_immediately_without_llm_credentials(
    pending_repo_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
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


async def test_submit_fill_blank_exact_match_never_calls_llm(
    pending_repo_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq_id, fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        response = client.patch(f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "arq"})

    assert response.status_code == 200
    assert response.json()["score"] == 1.0


async def test_submit_fill_blank_concept_miss_returns_503_without_llm_credentials(
    pending_repo_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq_id, fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        async with async_session_factory() as session:
            question = await session.get(Question, fb_id)
            assert question is not None
            question.fill_blank_mode = FillBlankMode.concept
            session.add(question)
            await session.commit()
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        response = client.patch(
            f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "some queueing thing"}
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "concept-mode grading is unavailable until an LLM provider is configured"


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


async def test_get_attempt_withholds_answers_while_in_progress(pending_repo_factory) -> None:
    # #34: even for a question that's *already been answered*, the answer
    # key must stay hidden until the whole attempt completes — otherwise a
    # mid-quiz GET /attempts/{id} could reveal correct_answer for questions
    # not yet reached.
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})

        response = client.get(f"/attempts/{attempt['id']}")

    assert response.status_code == 200
    answered = next(q for q in response.json()["questions"] if q["question_id"] == str(mcq_id))
    assert answered["score"] == 1.0  # already graded and visible via the PATCH response...
    assert answered["submitted_answer"] is None  # ...but not through this bulk endpoint pre-completion
    assert answered["correct_answer"] is None


async def test_complete_attempt_includes_submitted_and_correct_answer_text(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        # Deliberately wrong mcq choice (index 0, correct is index 1) and a
        # correct fill_blank answer, to prove both fields resolve
        # independently of whether the submission was right.
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 0})
        client.patch(f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "arq"})

        response = client.post(f"/attempts/{attempt['id']}/complete")

    assert response.status_code == 200
    questions = {q["question_id"]: q for q in response.json()["questions"]}

    mcq_result = questions[str(mcq_id)]
    assert mcq_result["submitted_answer"] == "a"  # choices[0], what was actually selected
    assert mcq_result["correct_answer"] == "b"  # choices[1], correct_index

    fb_result = questions[str(fb_id)]
    assert fb_result["submitted_answer"] == "arq"
    assert fb_result["correct_answer"] == "arq"


async def test_complete_attempt_answer_text_null_for_unanswered_question(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})

        response = client.post(f"/attempts/{attempt['id']}/complete")

    assert response.status_code == 200
    unanswered = next(q for q in response.json()["questions"] if q["question_id"] == str(fb_id))
    assert unanswered["submitted_answer"] is None
    # The correct answer is still safe to reveal once the whole attempt is
    # done — nothing is "upcoming" anymore at that point.
    assert unanswered["correct_answer"] == "arq"


async def test_create_attempt_is_idempotent_for_in_progress_attempt(pending_repo_factory) -> None:
    # StrictMode's double mount-effect invocation, a reload, or a second tab
    # must all resume the same attempt rather than minting a fresh one and
    # orphaning whatever was already answered (Phase 5 Codex review).
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, _mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))

        first = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        second = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

    assert first["id"] == second["id"]


async def test_create_attempt_starts_a_new_one_after_completion(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))

        first = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{first['id']}/answers/{mcq_id}", json={"selected_index": 1})
        client.post(f"/attempts/{first['id']}/complete")

        second = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

    assert second["id"] != first["id"]
    assert second["status"] == "in_progress"


async def test_submit_answer_rejects_oversized_answer_text(pending_repo_factory) -> None:
    app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider([])  # would raise if called
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, _mcq_id, fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

            response = client.patch(
                f"/attempts/{attempt['id']}/answers/{fb_id}",
                json={"answer_text": "x" * (MAX_ANSWER_TEXT_CHARS + 1)},
            )

        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)


async def test_submit_answer_upsert_does_not_duplicate_row(pending_repo_factory) -> None:
    # Two PATCHes for the same question (a resubmit, or — outside this
    # single-threaded test — a genuine race) must leave exactly one
    # AnswerSubmission row: the unique constraint plus the attempt-row lock
    # in _owned_attempt_for_update together guarantee this (Phase 5 Codex
    # review).
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, uuid.UUID(user["id"]))
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()

        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 0})
        client.patch(f"/attempts/{attempt['id']}/answers/{mcq_id}", json={"selected_index": 1})

        async with async_session_factory() as session:
            rows = list(
                (
                    await session.exec(
                        select(AnswerSubmission).where(
                            AnswerSubmission.attempt_id == uuid.UUID(attempt["id"]),
                            AnswerSubmission.question_id == mcq_id,
                        )
                    )
                ).all()
            )

    assert len(rows) == 1
    assert rows[0].selected_index == 1  # the later submission won


async def test_complete_results_include_working_citation(pending_repo_factory) -> None:
    with TestClient(app) as client:
        user = register_test_user(client)
        repo_id, snapshot_id = await pending_repo_factory(
            SourceType.git_url, "https://example.com/repo.git", user_id=uuid.UUID(user["id"])
        )
        async with async_session_factory() as session:
            guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
            session.add(guide)
            await session.flush()
            section = Section(
                study_guide_id=guide.id, section_type=SectionType.deep_dive, title="Deep Dives", order=0, content_md="x"
            )
            session.add(section)
            await session.flush()
            citation = Citation(
                section_id=section.id, file_path="app.py", line_start=1, line_end=2,
                claim_excerpt="the real claim", snippet_text="import arq",
            )
            session.add(citation)
            await session.flush()
            quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready)
            session.add(quiz)
            await session.flush()
            question = Question(
                quiz_id=quiz.id, question_type=QuestionType.mcq, order=0, prompt="q1",
                choices=["a", "b"], correct_index=0, explanation="e",
                file_path="app.py", line_start=1, line_end=2, source_citation_id=citation.id,
                prompt_version="v1", model="fake-model",
            )
            session.add(question)
            await session.commit()
            quiz_id, question_id = quiz.id, question.id

        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        client.patch(f"/attempts/{attempt['id']}/answers/{question_id}", json={"selected_index": 0})
        response = client.post(f"/attempts/{attempt['id']}/complete")

    assert response.status_code == 200
    result_question = response.json()["questions"][0]
    assert result_question["citation_claim_excerpt"] == "the real claim"
    assert result_question["citation_snippet_text"] == "import arq"


async def test_attempt_partial_unique_index_rejects_second_in_progress_row(pending_repo_factory) -> None:
    # The DB-level guarantee create_attempt's IntegrityError-recovery path
    # depends on: two in_progress Attempt rows for the same (quiz, user)
    # must be impossible, not just discouraged by the app-level check
    # (Phase 5 Codex review, second pass).
    with TestClient(app) as client:
        user = register_test_user(client)
    user_id = uuid.UUID(user["id"])
    quiz_id, _mcq_id, _fb_id = await _make_quiz_with_questions(pending_repo_factory, user_id)

    async with async_session_factory() as session:
        session.add(Attempt(quiz_id=quiz_id, user_id=user_id))
        await session.commit()

        session.add(Attempt(quiz_id=quiz_id, user_id=user_id))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_submit_answer_identical_retry_does_not_regrade(pending_repo_factory) -> None:
    # An ordinary HTTP retry resubmitting the exact same answer must not
    # incur a second paid grade_fill_blank call (Phase 5 Codex review,
    # second pass) — the FakeLLMProvider below is seeded with exactly one
    # response, so a second call would raise from running out.
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
            first = client.patch(
                f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "some queueing thing"}
            )
            second = client.patch(
                f"/attempts/{attempt['id']}/answers/{fb_id}", json={"answer_text": "some queueing thing"}
            )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert len(fake_llm.calls) == 1  # not 2 — would have raised on a second call anyway
    finally:
        app.dependency_overrides.pop(get_llm_provider, None)

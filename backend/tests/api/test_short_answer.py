"""short_answer questions end to end through the attempts API: judged
against the rubric, coverage persisted, revealed on the same terms as
feedback/answer key, and accepted by the transcription route.

See tests/api/test_repos.py's module docstring for the `with TestClient`
pattern."""

import uuid

from fastapi.testclient import TestClient
from sqlmodel import select

from app.api.attempts import get_llm_provider
from app.audio.dependencies import get_transcription_provider
from app.audio.fakes import FakeTranscriptionProvider
from app.audio.providers import TranscriptionResult
from app.db.models import (
    AnswerSubmission,
    FeedbackMode,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    SourceType,
    StudyGuide,
)
from app.db.session import async_session_factory
from app.main import app
from app.quizzing.grading.short_answer_grader import ShortAnswerGradeOutput
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from tests.conftest import register_test_user

RUBRIC = ["the pipeline takes seconds to minutes", "a request would time out", "status is persisted for polling"]


async def _quiz(pending_repo_factory, user_id: uuid.UUID, feedback_mode: FeedbackMode) -> tuple[uuid.UUID, uuid.UUID]:
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git", user_id=user_id)
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.flush()
        quiz = Quiz(repo_id=repo_id, study_guide_id=guide.id, status=QuizStatus.ready, feedback_mode=feedback_mode)
        session.add(quiz)
        await session.flush()
        question = Question(
            quiz_id=quiz.id, question_type=QuestionType.short_answer, order=0,
            prompt="Why does indexing run in a background job rather than in the HTTP request?",
            correct_answer="Cloning and parsing take minutes; a request would time out; the snapshot status is what the UI polls.",
            rubric=RUBRIC, file_path="app/api/repos.py", line_start=1, line_end=2, prompt_version="v1", model="fake",
        )
        session.add(question)
        await session.commit()
        return quiz.id, question.id


def _judge(hits: list[bool], feedback: str) -> FakeLLMProvider:
    return FakeLLMProvider(
        [LLMResponse(text="", parsed=ShortAnswerGradeOutput(hits=hits, feedback=feedback), model="fake", stop_reason="end_turn", usage={})]
    )


def _with_judge(fake: FakeLLMProvider):
    app.dependency_overrides[get_llm_provider] = lambda: fake
    return fake


def _clear():
    app.dependency_overrides.pop(get_llm_provider, None)
    app.dependency_overrides.pop(get_transcription_provider, None)


async def test_immediate_mode_grades_against_the_rubric_and_reveals_coverage(pending_repo_factory) -> None:
    judge = _with_judge(_judge([True, False, True], "You got the timing and the polling; the missing idea is the timeout."))
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, qid = await _quiz(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.immediate)
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            body = client.patch(
                f"/attempts/{attempt['id']}/answers/{qid}",
                json={"answer_text": "It takes a while to clone and parse, and the UI polls the status row."},
            ).json()

            async with async_session_factory() as session:
                sub = (await session.exec(select(AnswerSubmission).where(AnswerSubmission.question_id == qid))).one()
    finally:
        _clear()

    assert body["score"] == 2 / 3
    assert body["rubric"] == RUBRIC
    assert body["rubric_hits"] == [True, False, True]
    assert body["feedback"].startswith("You got")
    # The model answer rides in correct_answer, shown after grading.
    assert body["correct_answer"].startswith("Cloning and parsing")
    # Persisted, so results can show coverage later.
    assert sub.rubric_hits == [True, False, True]
    assert sub.score == 2 / 3
    # The judge saw the learner's words and the numbered rubric.
    sent = judge.calls[0].messages[0].content
    assert "the UI polls the status row" in sent
    assert "1. the pipeline takes seconds to minutes" in sent


async def test_end_of_quiz_mode_withholds_rubric_and_coverage_until_completion(pending_repo_factory) -> None:
    _with_judge(_judge([True, True, True], "Complete."))
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, qid = await _quiz(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.end_of_quiz)
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            submitted = client.patch(f"/attempts/{attempt['id']}/answers/{qid}", json={"answer_text": "an answer"}).json()
            during = client.get(f"/attempts/{attempt['id']}").json()["questions"][0]
            client.post(f"/attempts/{attempt['id']}/complete")
            after = client.get(f"/attempts/{attempt['id']}").json()["questions"][0]
    finally:
        _clear()

    # Nothing about correctness leaks before completion — the rubric IS the
    # answer key, and coverage implies the score.
    assert submitted["score"] is None and submitted["rubric"] is None and submitted["rubric_hits"] is None
    assert during["answered"] is True
    assert during["rubric"] is None and during["rubric_hits"] is None and during["correct_answer"] is None
    # All of it after.
    assert after["rubric"] == RUBRIC
    assert after["rubric_hits"] == [True, True, True]
    assert after["score"] == 1.0
    assert after["correct_answer"].startswith("Cloning and parsing")


async def test_a_misaligned_judge_verdict_is_a_503_and_persists_nothing(pending_repo_factory) -> None:
    _with_judge(_judge([True], "?"))  # rubric has 3 points
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, qid = await _quiz(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.immediate)
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            response = client.patch(f"/attempts/{attempt['id']}/answers/{qid}", json={"answer_text": "x"})
            results = client.get(f"/attempts/{attempt['id']}").json()
    finally:
        _clear()
    assert response.status_code == 503
    assert results["questions"][0]["answered"] is False


async def test_503_without_an_llm_since_there_is_no_exact_match_path(pending_repo_factory, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", None)
    with TestClient(app) as client:
        user = register_test_user(client)
        quiz_id, qid = await _quiz(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.immediate)
        attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
        response = client.patch(f"/attempts/{attempt['id']}/answers/{qid}", json={"answer_text": "x"})
    assert response.status_code == 503


async def test_spoken_answers_work_for_short_answer_and_hints_exclude_rubric_identifiers(pending_repo_factory) -> None:
    fake = FakeTranscriptionProvider([TranscriptionResult(text="because it is slow", model="f", language="en", duration_seconds=1.0)])
    app.dependency_overrides[get_transcription_provider] = lambda: fake
    try:
        with TestClient(app) as client:
            user = register_test_user(client)
            quiz_id, qid = await _quiz(pending_repo_factory, uuid.UUID(user["id"]), FeedbackMode.immediate)
            # Give the rubric an identifier so the exclusion has something to bite on.
            async with async_session_factory() as session:
                q = await session.get(Question, qid)
                q.rubric = [*RUBRIC, "run_layer_b does the semantic pass"]
                session.add(q)
                await session.commit()
            attempt = client.post("/attempts", json={"quiz_id": str(quiz_id)}).json()
            response = client.post(
                f"/attempts/{attempt['id']}/answers/{qid}/transcription",
                files={"file": ("a.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 32, "audio/webm")},
            )
    finally:
        _clear()

    assert response.status_code == 200, response.text
    assert response.json()["text"] == "because it is slow"
    assert "run_layer_b" not in fake.calls[0].vocabulary

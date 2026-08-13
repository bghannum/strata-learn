"""POST /attempts, PATCH .../answers/{qid}, POST .../complete — taking a quiz
and getting graded, live. Each answer is graded the instant it's submitted
(§10.1/§10.2), not deferred to completion — a fill_blank concept-mode miss
needs one real LLM call (grading/fill_blank_grader.py's judge fallback), made
directly from this request rather than through the arq/worker path
quizzes.py's generation job uses: it's a single cheap call gating one HTTP
response, not a multi-call batch job worth decoupling from the request.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import get_current_user
from app.config import settings
from app.db.models import (
    AnswerSubmission,
    Attempt,
    AttemptStatus,
    Question,
    QuestionType,
    Quiz,
    Repo,
    User,
)
from app.db.session import get_session
from app.quizzing.grading.fill_blank_grader import grade_fill_blank
from app.quizzing.grading.mcq_grader import grade_mcq
from app.semantics.llm_provider import AnthropicProvider, LLMProvider

router = APIRouter(prefix="/attempts", tags=["attempts"])


def get_llm_provider() -> LLMProvider:
    # A plain function dependency (not a class/singleton) so tests can swap
    # it via app.dependency_overrides for a FakeLLMProvider, the same
    # injection seam semantics/*.py's own tests use — just wired through
    # FastAPI's DI instead of a direct call argument, since this is invoked
    # from request handlers, not other application code.
    return AnthropicProvider(api_key=settings.anthropic_api_key)  # type: ignore[arg-type]


class CreateAttemptIn(BaseModel):
    quiz_id: UUID


class AttemptOut(BaseModel):
    id: UUID
    quiz_id: UUID
    status: str
    score: float | None


class AnswerIn(BaseModel):
    selected_index: int | None = None
    answer_text: str | None = None


class AnswerResultOut(BaseModel):
    question_id: UUID
    score: float
    feedback: str
    correct_index: int | None
    correct_answer: str | None


class QuestionResultOut(BaseModel):
    question_id: UUID
    question_type: str
    prompt: str
    score: float | None
    feedback: str | None
    file_path: str
    line_start: int
    line_end: int


class AttemptResultsOut(BaseModel):
    id: UUID
    quiz_id: UUID
    status: str
    score: float | None
    questions: list[QuestionResultOut]


async def _owned_attempt(session: AsyncSession, attempt_id: UUID, current_user: User) -> Attempt:
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None or attempt.user_id != current_user.id:
        raise HTTPException(404, "attempt not found")
    return attempt


async def _build_results(session: AsyncSession, attempt: Attempt) -> AttemptResultsOut:
    questions = list(
        (await session.exec(select(Question).where(Question.quiz_id == attempt.quiz_id).order_by(Question.order))).all()
    )
    submissions = list(
        (await session.exec(select(AnswerSubmission).where(AnswerSubmission.attempt_id == attempt.id))).all()
    )
    submission_by_question = {s.question_id: s for s in submissions}

    return AttemptResultsOut(
        id=attempt.id,
        quiz_id=attempt.quiz_id,
        status=attempt.status.value,
        score=attempt.score,
        questions=[
            QuestionResultOut(
                question_id=q.id,
                question_type=q.question_type.value,
                prompt=q.prompt,
                score=submission_by_question[q.id].score if q.id in submission_by_question else None,
                feedback=submission_by_question[q.id].feedback if q.id in submission_by_question else None,
                file_path=q.file_path,
                line_start=q.line_start,
                line_end=q.line_end,
            )
            for q in questions
        ],
    )


@router.get("/{attempt_id}", response_model=AttemptResultsOut)
async def get_attempt(
    attempt_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> AttemptResultsOut:
    # Not in the original plan's §12 Phase 5 endpoint list, but the
    # pre-scaffolded /attempts/:attemptId frontend route (App.tsx) needs a
    # way to load results on a direct visit or refresh, not only from
    # whatever POST /complete happened to return to the tab that called it.
    attempt = await _owned_attempt(session, attempt_id, current_user)
    return await _build_results(session, attempt)


@router.post("", response_model=AttemptOut, status_code=201)
async def create_attempt(
    body: CreateAttemptIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AttemptOut:
    quiz = await session.get(Quiz, body.quiz_id)
    if quiz is None:
        raise HTTPException(404, "quiz not found")
    repo = await session.get(Repo, quiz.repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "quiz not found")

    attempt = Attempt(quiz_id=quiz.id, user_id=current_user.id)
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    return AttemptOut(id=attempt.id, quiz_id=attempt.quiz_id, status=attempt.status.value, score=attempt.score)


@router.patch("/{attempt_id}/answers/{question_id}", response_model=AnswerResultOut)
async def submit_answer(
    attempt_id: UUID,
    question_id: UUID,
    body: AnswerIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    llm: LLMProvider = Depends(get_llm_provider),
) -> AnswerResultOut:
    attempt = await _owned_attempt(session, attempt_id, current_user)
    if attempt.status != AttemptStatus.in_progress:
        raise HTTPException(409, "attempt is already completed")

    question = await session.get(Question, question_id)
    if question is None or question.quiz_id != attempt.quiz_id:
        raise HTTPException(404, "question not found on this attempt's quiz")

    if question.question_type == QuestionType.mcq:
        if body.selected_index is None or not (0 <= body.selected_index < len(question.choices or [])):
            raise HTTPException(422, "selected_index is required and must be a valid choice index for an mcq question")
        score, feedback = grade_mcq(question, body.selected_index)
    else:
        if not body.answer_text or not body.answer_text.strip():
            raise HTTPException(422, "answer_text is required for a fill_blank question")
        score, feedback = await grade_fill_blank(llm, question, body.answer_text)

    existing = (
        await session.exec(
            select(AnswerSubmission).where(
                AnswerSubmission.attempt_id == attempt_id, AnswerSubmission.question_id == question_id
            )
        )
    ).first()
    submission = existing or AnswerSubmission(attempt_id=attempt_id, question_id=question_id)
    submission.selected_index = body.selected_index
    submission.answer_text = body.answer_text
    submission.score = score
    submission.feedback = feedback
    session.add(submission)
    await session.commit()

    return AnswerResultOut(
        question_id=question.id,
        score=score,
        feedback=feedback,
        correct_index=question.correct_index,
        correct_answer=question.correct_answer,
    )


@router.post("/{attempt_id}/complete", response_model=AttemptResultsOut)
async def complete_attempt(
    attempt_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AttemptResultsOut:
    attempt = await _owned_attempt(session, attempt_id, current_user)
    if attempt.status != AttemptStatus.in_progress:
        raise HTTPException(409, "attempt is already completed")

    questions = list(
        (await session.exec(select(Question).where(Question.quiz_id == attempt.quiz_id).order_by(Question.order))).all()
    )
    submissions = list(
        (await session.exec(select(AnswerSubmission).where(AnswerSubmission.attempt_id == attempt_id))).all()
    )
    submission_by_question = {s.question_id: s for s in submissions}

    if not submission_by_question:
        raise HTTPException(422, "answer at least one question before completing the attempt")

    # Unanswered questions count as 0 rather than blocking completion — lets
    # a student submit early without every question being mandatory, at the
    # cost of a lower score. Averaged over all questions in the quiz, not
    # just the answered ones, so an early finish is scored honestly.
    total_score = sum(submission_by_question[q.id].score or 0.0 for q in questions if q.id in submission_by_question)
    attempt.score = total_score / len(questions) if questions else 0.0
    attempt.status = AttemptStatus.completed
    attempt.completed_at = datetime.now(UTC)
    session.add(attempt)
    await session.commit()

    return await _build_results(session, attempt)

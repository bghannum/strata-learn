"""POST /quizzes/{repo_id}/generate creates a pending Quiz row synchronously,
then enqueues quizzing/generation.py's work as an arq job and returns
immediately — same shape as POST /repos (api/repos.py). GET /quizzes/{id}
is the client's poll target (see worker/quiz_pipeline.py's docstring for why
this uses polling, not a progress WebSocket, unlike repo indexing).

GET /quizzes/{id} never includes an answer key (correct_index,
correct_answer, acceptable_alternatives, explanation) — those only appear in
PATCH /attempts/{id}/answers/{qid}'s response, after the student has already
submitted that specific question. Shipping the answer key up front in the
quiz payload would make the whole exercise pointless (visible in the browser
network tab before the student even answers).
"""

from uuid import UUID

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import get_current_user
from app.db.models import FeedbackMode, Question, Quiz, QuizStatus, Repo, StudyGuide, User
from app.db.session import get_session
from app.quizzing.generation import create_pending_quiz, fail_quiz
from app.redis_pool import get_redis_pool

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


class QuestionOut(BaseModel):
    id: UUID
    question_type: str
    order: int
    prompt: str
    choices: list[str] | None
    fill_blank_mode: str | None


class QuizOut(BaseModel):
    id: UUID
    repo_id: UUID
    study_guide_id: UUID
    status: str
    feedback_mode: str
    questions: list[QuestionOut]


class GenerateQuizIn(BaseModel):
    # #37: ui-spec.md §6.5's per-quiz feedback-timing toggle. Defaults to
    # end_of_quiz (matching FeedbackMode's own default) so an empty POST
    # body — what every caller sent before this field existed — keeps
    # working unchanged.
    feedback_mode: FeedbackMode = FeedbackMode.end_of_quiz


async def _owned_repo(session: AsyncSession, repo_id: UUID, current_user: User) -> Repo:
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.user_id != current_user.id:
        raise HTTPException(404, "repo not found")
    return repo


@router.post("/{repo_id}/generate", response_model=QuizOut, status_code=201)
async def generate_quiz_endpoint(
    repo_id: UUID,
    body: GenerateQuizIn = GenerateQuizIn(),
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis_pool),
    current_user: User = Depends(get_current_user),
) -> QuizOut:
    repo = await _owned_repo(session, repo_id, current_user)
    if repo.latest_snapshot_id is None:
        raise HTTPException(409, "repo has no snapshot yet")

    # Same lookup api/repos.py's GET /repos/{id}/study-guide redirect uses —
    # a quiz is generated from the current study guide, so one must already
    # exist and be ready.
    guide = (
        await session.exec(select(StudyGuide).where(StudyGuide.snapshot_id == repo.latest_snapshot_id))
    ).first()
    if guide is None:
        raise HTTPException(409, "study guide not ready yet — generate one before requesting a quiz")

    quiz, created = await create_pending_quiz(session, repo_id, guide.id, feedback_mode=body.feedback_mode)

    if created:
        try:
            await redis.enqueue_job("generate_quiz", quiz_id=quiz.id, study_guide_id=guide.id)
        except Exception as exc:
            # quiz is already committed above — same "don't strand it silently"
            # reasoning as api/repos.py's own enqueue try/except.
            await fail_quiz(session, quiz.id)
            raise HTTPException(503, "Could not queue quiz generation — try again shortly") from exc
    # created=False means a `generating` quiz for this study guide already
    # existed (this call reused it) — a second enqueue would double the
    # billed LLM calls for the same quiz (found via the Phase 5 Codex
    # review, second pass).

    return QuizOut(
        id=quiz.id,
        repo_id=quiz.repo_id,
        study_guide_id=quiz.study_guide_id,
        status=quiz.status.value,
        feedback_mode=quiz.feedback_mode.value,
        questions=[],
    )


@router.get("/{quiz_id}", response_model=QuizOut)
async def get_quiz(
    quiz_id: UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)
) -> QuizOut:
    quiz = await session.get(Quiz, quiz_id)
    if quiz is None:
        raise HTTPException(404, "quiz not found")
    # Same "404, not 403" reasoning as api/study_guides.py's ownership check.
    await _owned_repo(session, quiz.repo_id, current_user)

    questions: list[Question] = []
    if quiz.status == QuizStatus.ready:
        questions = list(
            (await session.exec(select(Question).where(Question.quiz_id == quiz.id).order_by(Question.order))).all()
        )

    return QuizOut(
        id=quiz.id,
        repo_id=quiz.repo_id,
        study_guide_id=quiz.study_guide_id,
        status=quiz.status.value,
        feedback_mode=quiz.feedback_mode.value,
        questions=[
            QuestionOut(
                id=q.id,
                question_type=q.question_type.value,
                order=q.order,
                prompt=q.prompt,
                choices=q.choices,
                fill_blank_mode=q.fill_blank_mode.value if q.fill_blank_mode else None,
            )
            for q in questions
        ],
    )

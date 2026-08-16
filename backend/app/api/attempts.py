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

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.auth import get_current_user
from app.audio import rate_limit
from app.audio.dependencies import get_transcription_provider
from app.audio.errors import AudioProviderError, AudioValidationError
from app.audio.providers import AudioClip, TranscriptionProvider
from app.audio.validation import validate_audio_upload
from app.audio.vocabulary import build_vocabulary
from app.config import settings
from app.db.models import (
    AnswerSubmission,
    Attempt,
    AttemptStatus,
    Citation,
    FeedbackMode,
    Question,
    QuestionType,
    Quiz,
    Repo,
    StudyGuide,
    Subsystem,
    User,
)
from app.db.session import get_session
from app.quizzing.grading.fill_blank_grader import (
    FillBlankLLMUnavailableError,
    grade_fill_blank,
)
from app.quizzing.grading.mcq_grader import grade_mcq
from app.redis_pool import get_redis_pool
from app.semantics.llm_provider import AnthropicProvider, LLMOutputError, LLMProvider

router = APIRouter(prefix="/attempts", tags=["attempts"])

# Bounds the text handed to grade_fill_blank's LLM-judge fallback — an
# unbounded answer_text is stored as-is and interpolated directly into a
# paid Anthropic call; a pasted or crafted oversized answer would inflate
# billed input tokens for no grading benefit (found via the Phase 5 Codex
# review). Generous for a genuine one-word-to-one-sentence fill-blank answer.
MAX_ANSWER_TEXT_CHARS = 2000


def get_llm_provider() -> LLMProvider | None:
    # A plain function dependency (not a class/singleton) so tests can swap
    # it via app.dependency_overrides for a FakeLLMProvider, the same
    # injection seam semantics/*.py's own tests use — just wired through
    # FastAPI's DI instead of a direct call argument, since this is invoked
    # from request handlers, not other application code.
    # Most answer submissions are deterministic. Do not construct a paid
    # provider merely because FastAPI resolves dependencies before the route
    # knows the question type; credential-free MCQ, exact-match, and code-mode
    # grading must keep working in CI and in a partially configured app.
    if not settings.anthropic_api_key:
        return None
    return AnthropicProvider(api_key=settings.anthropic_api_key)


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
    # #37, found via Codex's PR #50 review: nullable, not always populated.
    # In end_of_quiz mode this response must not reveal correctness at all
    # (score alone is enough to imply it) — the answer is still graded and
    # persisted server-side, just not echoed back here. See submit_answer.
    score: float | None
    feedback: str | None
    correct_index: int | None
    correct_answer: str | None


class QuestionResultOut(BaseModel):
    question_id: UUID
    question_type: str
    prompt: str
    # Independent of score/feedback below — true whenever a submission
    # exists, regardless of whether feedback_mode allows revealing it yet.
    # QuizTaker.tsx's resume logic needs this signal without needing the
    # (possibly withheld) score to detect "already answered".
    answered: bool
    score: float | None
    feedback: str | None
    file_path: str
    line_start: int
    line_end: int
    # The actual cited claim/snippet from the study guide's own Citation row
    # this question was generated from — a bare file_path:line_start-line_end
    # is a path, not a *working* citation back to the study guide's evidence
    # (found via the Phase 5 Codex review; ui-spec.md §6.7 asks for exactly
    # this link). Null only if the source Citation has since been deleted.
    citation_claim_excerpt: str | None
    citation_snippet_text: str | None
    # #34, ui-spec.md §6.7: the student's own answer and the correct one, as
    # display text (an mcq choice's text, not its bare index — resolved here
    # so the frontend doesn't need question.choices just to render this).
    # Both null while the attempt is still in_progress, regardless of
    # whether a given question has already been answered — GET
    # /attempts/{id} must never let a mid-quiz fetch reveal the correct
    # answer to a question the student hasn't reached yet, and gating
    # uniformly by attempt status keeps that contract simple to reason
    # about rather than leaking it question-by-question.
    submitted_answer: str | None = None
    correct_answer: str | None = None


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


async def _owned_attempt_for_update(session: AsyncSession, attempt_id: UUID, current_user: User) -> Attempt:
    """Like _owned_attempt, but locks the row (`SELECT ... FOR UPDATE`) for
    the rest of this transaction. submit_answer and complete_attempt both use
    this — without it, a slow concept-mode grading call in one request and a
    concurrent call to the other endpoint can both read `in_progress` before
    either commits, so an answer can land after completion and be silently
    excluded from the already-computed score (found via the Phase 5 Codex
    review). The lock makes the two endpoints serialize on a given attempt
    rather than race; unrelated attempts are unaffected."""
    attempt = (
        await session.exec(select(Attempt).where(Attempt.id == attempt_id).with_for_update())
    ).first()
    if attempt is None or attempt.user_id != current_user.id:
        raise HTTPException(404, "attempt not found")
    return attempt


def _answer_display_text(question: Question, selected_index: int | None, answer_text: str | None) -> str | None:
    """Resolves a stored submission to display text — an mcq choice's text,
    not its bare index, so QuestionResultOut doesn't need to also carry
    question.choices just for the frontend to look this up itself."""
    if question.question_type == QuestionType.mcq:
        if selected_index is None or question.choices is None or not (0 <= selected_index < len(question.choices)):
            return None
        return question.choices[selected_index]
    return answer_text


def _correct_answer_display_text(question: Question) -> str | None:
    if question.question_type == QuestionType.mcq:
        if question.correct_index is None or question.choices is None or not (
            0 <= question.correct_index < len(question.choices)
        ):
            return None
        return question.choices[question.correct_index]
    return question.correct_answer


async def _build_results(session: AsyncSession, attempt: Attempt) -> AttemptResultsOut:
    quiz = await session.get(Quiz, attempt.quiz_id)
    questions = list(
        (await session.exec(select(Question).where(Question.quiz_id == attempt.quiz_id).order_by(Question.order))).all()
    )
    submissions = list(
        (await session.exec(select(AnswerSubmission).where(AnswerSubmission.attempt_id == attempt.id))).all()
    )
    submission_by_question = {s.question_id: s for s in submissions}

    citation_ids = [q.source_citation_id for q in questions if q.source_citation_id is not None]
    citation_by_id: dict[UUID, Citation] = {}
    if citation_ids:
        citations = (await session.exec(select(Citation).where(Citation.id.in_(citation_ids)))).all()
        citation_by_id = {c.id: c for c in citations}

    # #34: submitted_answer/correct_answer only once the attempt is
    # completed — see QuestionResultOut's comment for why this is gated
    # uniformly rather than per-question.
    is_completed = attempt.status == AttemptStatus.completed
    # #37, found via Codex's PR #50 review: score/feedback need their own,
    # looser gate. An answered question's score/feedback were already
    # revealed via submit_answer's own response at submit time in immediate
    # mode, so showing them again here isn't a new leak — but in
    # end_of_quiz mode they must stay hidden until completion, the whole
    # point of that mode. An unanswered question has no submission at all,
    # so it's None either way regardless of this flag.
    reveal_score_feedback = is_completed or quiz.feedback_mode == FeedbackMode.immediate

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
                answered=q.id in submission_by_question,
                score=submission_by_question[q.id].score
                if q.id in submission_by_question and reveal_score_feedback
                else None,
                feedback=submission_by_question[q.id].feedback
                if q.id in submission_by_question and reveal_score_feedback
                else None,
                file_path=q.file_path,
                line_start=q.line_start,
                line_end=q.line_end,
                citation_claim_excerpt=citation_by_id[q.source_citation_id].claim_excerpt
                if q.source_citation_id in citation_by_id
                else None,
                citation_snippet_text=citation_by_id[q.source_citation_id].snippet_text
                if q.source_citation_id in citation_by_id
                else None,
                submitted_answer=_answer_display_text(
                    q, submission_by_question[q.id].selected_index, submission_by_question[q.id].answer_text
                )
                if is_completed and q.id in submission_by_question
                else None,
                correct_answer=_correct_answer_display_text(q) if is_completed else None,
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


async def _find_in_progress_attempt(session: AsyncSession, quiz_id: UUID, user_id: UUID) -> Attempt | None:
    return (
        await session.exec(
            select(Attempt).where(
                Attempt.quiz_id == quiz_id, Attempt.user_id == user_id, Attempt.status == AttemptStatus.in_progress
            )
        )
    ).first()


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

    # Idempotent per (user, quiz), not a new row every call — React
    # StrictMode double-invokes mount effects in dev, and a page reload or
    # a second tab hitting QuizTaker for the same quiz would otherwise mint
    # a fresh, empty attempt each time and orphan whatever answers were
    # already submitted to the previous one (found via the Phase 5 Codex
    # review; ui-spec.md §6.5 calls for exactly this interruption-safety).
    #
    # This check alone isn't race-free — two concurrent calls (the exact
    # StrictMode/second-tab scenario it exists for) can both pass it before
    # either commits. Attempt's partial unique index (db/models.py) is the
    # real guarantee: a losing insert's IntegrityError is caught below and
    # turned into "resume the winner's row" (found via the Phase 5 Codex
    # review, second pass).
    existing = await _find_in_progress_attempt(session, quiz.id, current_user.id)
    if existing is not None:
        return AttemptOut(id=existing.id, quiz_id=existing.quiz_id, status=existing.status.value, score=existing.score)

    attempt = Attempt(quiz_id=quiz.id, user_id=current_user.id)
    session.add(attempt)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_in_progress_attempt(session, quiz.id, current_user.id)
        if existing is None:
            raise  # a real, different failure — don't mask it as "someone else won"
        return AttemptOut(id=existing.id, quiz_id=existing.quiz_id, status=existing.status.value, score=existing.score)
    await session.refresh(attempt)
    return AttemptOut(id=attempt.id, quiz_id=attempt.quiz_id, status=attempt.status.value, score=attempt.score)


@router.patch("/{attempt_id}/answers/{question_id}", response_model=AnswerResultOut)
async def submit_answer(
    attempt_id: UUID,
    question_id: UUID,
    body: AnswerIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    llm: LLMProvider | None = Depends(get_llm_provider),
) -> AnswerResultOut:
    attempt = await _owned_attempt_for_update(session, attempt_id, current_user)
    if attempt.status != AttemptStatus.in_progress:
        raise HTTPException(409, "attempt is already completed")

    question = await session.get(Question, question_id)
    if question is None or question.quiz_id != attempt.quiz_id:
        raise HTTPException(404, "question not found on this attempt's quiz")

    # #37, found via Codex's PR #50 review: in end_of_quiz mode, this
    # response must not reveal correctness at all — the answer is still
    # graded and persisted below either way, just not echoed back here.
    # Without this, inspecting the network response defeats the whole
    # point of the mode regardless of what QuizTaker.tsx chooses to render.
    quiz = await session.get(Quiz, attempt.quiz_id)
    reveal = quiz is not None and quiz.feedback_mode == FeedbackMode.immediate

    existing = (
        await session.exec(
            select(AnswerSubmission).where(
                AnswerSubmission.attempt_id == attempt_id, AnswerSubmission.question_id == question_id
            )
        )
    ).first()
    # Checked before grading, not after — an ordinary HTTP retry (a lost
    # response, a double-click) resubmitting the *same* answer must not
    # incur a second paid grade_fill_blank call for a concept-mode question
    # whose first attempt already committed (found via the Phase 5 Codex
    # review, second pass). A genuinely changed answer still re-grades below.
    if existing is not None and existing.selected_index == body.selected_index and existing.answer_text == body.answer_text:
        return AnswerResultOut(
            question_id=question.id,
            score=existing.score if reveal else None,
            feedback=existing.feedback if reveal else None,
            correct_index=question.correct_index if reveal else None,
            correct_answer=question.correct_answer if reveal else None,
        )

    if question.question_type == QuestionType.mcq:
        if body.selected_index is None or not (0 <= body.selected_index < len(question.choices or [])):
            raise HTTPException(422, "selected_index is required and must be a valid choice index for an mcq question")
        score, feedback = grade_mcq(question, body.selected_index)
    else:
        if not body.answer_text or not body.answer_text.strip():
            raise HTTPException(422, "answer_text is required for a fill_blank question")
        if len(body.answer_text) > MAX_ANSWER_TEXT_CHARS:
            raise HTTPException(422, f"answer_text exceeds the {MAX_ANSWER_TEXT_CHARS}-character limit")
        try:
            score, feedback = await grade_fill_blank(llm, question, body.answer_text)
        except FillBlankLLMUnavailableError as exc:
            raise HTTPException(503, "concept-mode grading is unavailable until an LLM provider is configured") from exc
        except LLMOutputError as exc:
            # The judge returned nothing gradable (typically a truncated
            # response). 503 rather than a fabricated score: no submission is
            # written, so the same answer can simply be sent again.
            raise HTTPException(503, "concept-mode grading did not return a usable result; please retry") from exc

    submission = existing or AnswerSubmission(attempt_id=attempt_id, question_id=question_id)
    submission.selected_index = body.selected_index
    submission.answer_text = body.answer_text
    submission.score = score
    submission.feedback = feedback
    session.add(submission)
    await session.commit()

    return AnswerResultOut(
        question_id=question.id,
        score=score if reveal else None,
        feedback=feedback if reveal else None,
        correct_index=question.correct_index if reveal else None,
        correct_answer=question.correct_answer if reveal else None,
    )


class TranscriptionOut(BaseModel):
    # No model or backend name: which one a deployment uses is operator
    # config the UI is deliberately kept ignorant of (ADR-010). Consistent
    # with GET /voice/capabilities' booleans-only answer.
    text: str
    duration_ms: int


async def _vocabulary_for_question(session: AsyncSession, question: Question, quiz: Quiz | None) -> list[str]:
    """Hint terms drawn from the code the question was generated *from* — its
    seed citation's snippet, its file path, its subsystem's name. Never from
    the answer key: correct_answer and acceptable_alternatives are handed to
    build_vocabulary as the *exclusion* list. See app/audio/vocabulary.py's
    docstring for why priming the ASR model with the answer would turn this
    into an answer oracle for exact-match grading."""
    snippet = None
    if question.source_citation_id is not None:
        citation = await session.get(Citation, question.source_citation_id)
        snippet = citation.snippet_text if citation is not None else None

    subsystem_name = None
    if question.subsystem_key and quiz is not None:
        guide = await session.get(StudyGuide, quiz.study_guide_id)
        if guide is not None:
            subsystem = (
                await session.exec(
                    select(Subsystem).where(
                        Subsystem.snapshot_id == guide.snapshot_id, Subsystem.key == question.subsystem_key
                    )
                )
            ).first()
            subsystem_name = subsystem.name if subsystem is not None else None

    return build_vocabulary(
        snippet_text=snippet,
        file_path=question.file_path,
        subsystem_name=subsystem_name,
        excluded_terms=[question.correct_answer or "", *(question.acceptable_alternatives or [])],
        max_terms=settings.transcription_vocab_max_terms,
        max_chars=settings.transcription_vocab_max_chars,
    )


@router.post("/{attempt_id}/answers/{question_id}/transcription", response_model=TranscriptionOut)
async def transcribe_answer(
    attempt_id: UUID,
    question_id: UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    redis: ArqRedis = Depends(get_redis_pool),
    transcriber: TranscriptionProvider | None = Depends(get_transcription_provider),
) -> TranscriptionOut:
    """A microphone recording in, an *editable* transcript out. Writes
    nothing: the learner reviews and corrects the text in QuizTaker.tsx and
    only the confirmed version goes through submit_answer above, exactly like
    a typed answer. Never grading an unconfirmed transcript is the phase's
    one non-negotiable (ADR-010).

    Nested under the answer path rather than a standalone /transcribe
    because the identifiers are what make this not a general-purpose paid
    ASR proxy: they are the authorization scope *and* the vocabulary source.

    Gate order is cheapest-and-least-revealing first, paid call last.
    """
    # 1. Ownership — a plain get, not the FOR UPDATE variant: nothing is
    #    written, so a row lock would only serialize transcription against
    #    grading for no benefit.
    attempt = await _owned_attempt(session, attempt_id, current_user)
    # 2. Same 409 as submit_answer — no point transcribing for a finished quiz.
    if attempt.status != AttemptStatus.in_progress:
        raise HTTPException(409, "attempt is already completed")
    # 3–4. The question is this quiz's, and has a text answer to transcribe into.
    question = await session.get(Question, question_id)
    if question is None or question.quiz_id != attempt.quiz_id:
        raise HTTPException(404, "question not found on this attempt's quiz")
    if question.question_type != QuestionType.fill_blank:
        raise HTTPException(422, "only fill_blank questions accept a spoken answer")
    # 5. Capability check before reading the body — a 503 for a disabled
    #    feature shouldn't cost a 2 MiB upload first. Generic detail: which
    #    backend (or none) is operator config, not something a caller learns
    #    from an error body.
    if transcriber is None:
        raise HTTPException(503, "spoken answers are unavailable on this deployment")
    # 6. Rate limit — counted before the paid call, so a burst that trips it
    #    isn't also billed.
    if not await rate_limit.check_and_count(redis, current_user.id, "transcription", settings.voice_calls_per_hour):
        raise HTTPException(429, "too many voice requests this hour — try again later")
    # 7. Bounded read, verbatim the zip-upload pattern in repos.py: read one
    #    byte past the cap so worst-case memory is bounded regardless of what
    #    the client sends, and validate_audio_upload sees max+1 as oversize.
    data = await file.read(settings.audio_upload_max_bytes + 1)
    # 8. Magic-byte allowlist. The declared content type is ignored on
    #    purpose — see app/audio/validation.py.
    try:
        fmt = validate_audio_upload(data, settings.audio_upload_max_bytes)
    except AudioValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    quiz = await session.get(Quiz, attempt.quiz_id)
    vocabulary = await _vocabulary_for_question(session, question, quiz)
    clip = AudioClip(data=data, content_type=fmt.content_type, filename=f"answer.{fmt.extension}")

    # 9. The paid call. A provider refusal is a 503 with nothing persisted,
    #    so the learner can simply re-record — same disposition as a
    #    concept-grading LLM failure in submit_answer.
    started = datetime.now(UTC)
    try:
        result = await transcriber.transcribe(clip, vocabulary=vocabulary)
    except AudioProviderError as exc:
        raise HTTPException(503, "transcription is temporarily unavailable — please try again") from exc
    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)

    return TranscriptionOut(text=result.text, duration_ms=duration_ms)


@router.post("/{attempt_id}/complete", response_model=AttemptResultsOut)
async def complete_attempt(
    attempt_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AttemptResultsOut:
    attempt = await _owned_attempt_for_update(session, attempt_id, current_user)
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

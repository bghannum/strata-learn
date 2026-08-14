import asyncio
import uuid

import pytest
from sqlmodel import select

from app.db.models import (
    Citation,
    Question,
    Quiz,
    QuizStatus,
    Section,
    SectionType,
    SourceType,
    StudyGuide,
)
from app.db.session import async_session_factory
from app.quizzing.fill_blank_generator import FillBlankOutput
from app.quizzing.mcq_generator import MCQOutput
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.worker.quiz_pipeline import generate_quiz


def _mcq_response() -> LLMResponse:
    return LLMResponse(
        text="", model="fake-model", stop_reason="end_turn", usage={},
        parsed=MCQOutput(prompt="q", choices=["a", "b"], correct_index=0, explanation="e"),
    )


def _fill_blank_response() -> LLMResponse:
    return LLMResponse(
        text="", model="fake-model", stop_reason="end_turn", usage={},
        parsed=FillBlankOutput(mode="code", blanked_text="uses ___", correct_answer="arq", acceptable_alternatives=[]),
    )


class _BoomLLMProvider:
    async def complete(self, *args, **kwargs):
        raise RuntimeError("simulated LLM crash")


class _CancelledLLMProvider:
    async def complete(self, *args, **kwargs):
        raise asyncio.CancelledError


async def _make_study_guide_with_two_citations(pending_repo_factory) -> tuple[uuid.UUID, uuid.UUID]:
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.flush()
        section = Section(
            study_guide_id=guide.id, section_type=SectionType.deep_dive, title="Deep Dives", order=0, content_md="x",
        )
        session.add(section)
        await session.flush()
        session.add(
            Citation(
                section_id=section.id, file_path="a.py", line_start=1, line_end=2,
                claim_excerpt="a does x", snippet_text="import x",
            )
        )
        session.add(
            Citation(
                section_id=section.id, file_path="b.py", line_start=1, line_end=2,
                claim_excerpt="b does y", snippet_text="import arq",
            )
        )
        await session.commit()
        return repo_id, guide.id


async def _make_study_guide_with_no_citations(pending_repo_factory) -> tuple[uuid.UUID, uuid.UUID]:
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=1)
        session.add(guide)
        await session.commit()
        return repo_id, guide.id


async def _make_pending_quiz(repo_id: uuid.UUID, study_guide_id: uuid.UUID, status=QuizStatus.generating) -> uuid.UUID:
    async with async_session_factory() as session:
        quiz = Quiz(repo_id=repo_id, study_guide_id=study_guide_id, status=status)
        session.add(quiz)
        await session.commit()
        await session.refresh(quiz)
        return quiz.id


async def test_generate_quiz_success_persists_questions_and_marks_ready(pending_repo_factory) -> None:
    # Two citations, one section — identify_question_seeds sorts "a.py"
    # before "b.py" (same priority tier), and generation.py alternates
    # mcq/fill_blank by seed position, so this exercises both generators in
    # one deterministic pass: mcq from a.py, fill_blank from b.py.
    repo_id, guide_id = await _make_study_guide_with_two_citations(pending_repo_factory)
    quiz_id = await _make_pending_quiz(repo_id, guide_id)
    llm = FakeLLMProvider([_mcq_response(), _fill_blank_response()])

    await generate_quiz({}, quiz_id=quiz_id, study_guide_id=guide_id, llm=llm)

    async with async_session_factory() as session:
        quiz = await session.get(Quiz, quiz_id)
        assert quiz.status == QuizStatus.ready
        questions = list((await session.exec(select(Question).where(Question.quiz_id == quiz_id))).all())
    assert len(questions) == 2
    assert {q.question_type.value for q in questions} == {"mcq", "fill_blank"}
    # Each question keeps a working link back to the Citation it was
    # generated from, not just a copied file_path/line range (Phase 5
    # Codex review — a range alone can't be traced to one Citation row
    # when the same range is cited by more than one Section).
    assert all(q.source_citation_id is not None for q in questions)


async def test_generate_quiz_with_no_citations_marks_failed(pending_repo_factory) -> None:
    # A guide too thin to seed any questions from must not end up `ready`
    # with zero questions — QuizTaker indexes straight into questions[0]
    # (Phase 5 Codex review).
    repo_id, guide_id = await _make_study_guide_with_no_citations(pending_repo_factory)
    quiz_id = await _make_pending_quiz(repo_id, guide_id)

    with pytest.raises(RuntimeError, match="no usable questions"):
        await generate_quiz({}, quiz_id=quiz_id, study_guide_id=guide_id, llm=FakeLLMProvider([]))

    async with async_session_factory() as session:
        quiz = await session.get(Quiz, quiz_id)
        assert quiz.status == QuizStatus.failed


async def test_generate_quiz_short_circuits_when_already_ready(pending_repo_factory) -> None:
    # arq is at-least-once — a redelivery of an already-`ready` job must not
    # re-run every billed LLM call (same reasoning as index_repo's own
    # "already ready" short-circuit).
    repo_id, guide_id = await _make_study_guide_with_two_citations(pending_repo_factory)
    quiz_id = await _make_pending_quiz(repo_id, guide_id, status=QuizStatus.ready)
    llm = _BoomLLMProvider()  # would raise if the generator ever called it

    await generate_quiz({}, quiz_id=quiz_id, study_guide_id=guide_id, llm=llm)

    async with async_session_factory() as session:
        quiz = await session.get(Quiz, quiz_id)
        assert quiz.status == QuizStatus.ready


async def test_generate_quiz_failure_marks_failed_and_reraises(pending_repo_factory) -> None:
    repo_id, guide_id = await _make_study_guide_with_two_citations(pending_repo_factory)
    quiz_id = await _make_pending_quiz(repo_id, guide_id)

    with pytest.raises(RuntimeError, match="simulated LLM crash"):
        await generate_quiz({}, quiz_id=quiz_id, study_guide_id=guide_id, llm=_BoomLLMProvider())

    async with async_session_factory() as session:
        quiz = await session.get(Quiz, quiz_id)
        assert quiz.status == QuizStatus.failed


async def test_generate_quiz_cancelled_marks_failed_and_reraises(pending_repo_factory) -> None:
    repo_id, guide_id = await _make_study_guide_with_two_citations(pending_repo_factory)
    quiz_id = await _make_pending_quiz(repo_id, guide_id)

    with pytest.raises(asyncio.CancelledError):
        await generate_quiz({}, quiz_id=quiz_id, study_guide_id=guide_id, llm=_CancelledLLMProvider())

    async with async_session_factory() as session:
        quiz = await session.get(Quiz, quiz_id)
        assert quiz.status == QuizStatus.failed

import asyncio
import uuid

import pytest
from sqlmodel import select

from app.db.models import (
    AnalysisSnapshot,
    Citation,
    Question,
    Quiz,
    QuizStatus,
    Section,
    SectionType,
    SnapshotStatus,
    SourceType,
    StudyGuide,
    Subsystem,
)
from app.db.session import async_session_factory
from app.quizzing.mcq_generator import MCQOutput
from app.quizzing.short_answer_generator import ShortAnswerOutput
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse
from app.worker.quiz_pipeline import generate_quiz


def _mcq_response() -> LLMResponse:
    return LLMResponse(
        text="", model="fake-model", stop_reason="end_turn", usage={},
        parsed=MCQOutput(prompt="q", choices=["a", "b"], correct_index=0, explanation="e"),
    )


def _short_answer_response() -> LLMResponse:
    return LLMResponse(
        text="", model="fake-model", stop_reason="end_turn", usage={},
        parsed=ShortAnswerOutput(
            prompt="Why does the worker use arq?", model_answer="Because jobs must outlive a request.",
            rubric=["jobs outlive the request", "redis is already present"],
        ),
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


async def _make_guide_with_citations(
    repo_id: uuid.UUID, snapshot_id: uuid.UUID, file_paths: list[str], version: int = 1
) -> uuid.UUID:
    """One deep-dive section citing each given path, with a distinct claim per
    citation so #51's claim dedup doesn't collapse them."""
    async with async_session_factory() as session:
        guide = StudyGuide(repo_id=repo_id, snapshot_id=snapshot_id, version=version)
        session.add(guide)
        await session.flush()
        section = Section(
            study_guide_id=guide.id, section_type=SectionType.deep_dive, title="Deep Dives", order=0, content_md="x",
        )
        session.add(section)
        await session.flush()
        for path in file_paths:
            session.add(
                Citation(
                    section_id=section.id, file_path=path, line_start=1, line_end=2,
                    claim_excerpt=f"{path} does something", snippet_text="import x",
                )
            )
        await session.commit()
        return guide.id


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
    # mcq/short_answer by seed position, so this exercises both generators in
    # one deterministic pass: mcq from a.py, short_answer from b.py.
    repo_id, guide_id = await _make_study_guide_with_two_citations(pending_repo_factory)
    quiz_id = await _make_pending_quiz(repo_id, guide_id)
    llm = FakeLLMProvider([_mcq_response(), _short_answer_response()])

    await generate_quiz({}, quiz_id=quiz_id, study_guide_id=guide_id, llm=llm)

    async with async_session_factory() as session:
        quiz = await session.get(Quiz, quiz_id)
        assert quiz.status == QuizStatus.ready
        questions = list((await session.exec(select(Question).where(Question.quiz_id == quiz_id))).all())
    assert len(questions) == 2
    assert {q.question_type.value for q in questions} == {"mcq", "short_answer"}
    short = next(q for q in questions if q.question_type.value == "short_answer")
    # The rubric is what the grader judges; the model answer rides in
    # correct_answer for the results view.
    assert short.rubric == ["jobs outlive the request", "redis is already present"]
    assert short.correct_answer == "Because jobs must outlive a request."
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


async def _add_subsystem(snapshot_id: uuid.UUID, key: str, name: str, file_paths: list[str]) -> None:
    async with async_session_factory() as session:
        session.add(
            Subsystem(
                snapshot_id=snapshot_id, key=key, name=name, role="r",
                file_paths=file_paths, depth=0, order=0, prompt_version="v1", model="fake-model",
            )
        )
        await session.commit()


async def test_generated_questions_carry_their_subsystem_key(pending_repo_factory) -> None:
    # #61: mastery aggregates on this key, because Section/Question ids are all
    # replaced by a re-index and can't join scores across versions.
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")
    guide_id = await _make_guide_with_citations(repo_id, snapshot_id, ["app/api/a.py", "app/worker/b.py"])
    await _add_subsystem(snapshot_id, "app/api", "HTTP API", ["app/api/a.py"])
    await _add_subsystem(snapshot_id, "app/worker", "Background worker", ["app/worker/b.py"])
    quiz_id = await _make_pending_quiz(repo_id, guide_id)

    await generate_quiz(
        {}, quiz_id=quiz_id, study_guide_id=guide_id,
        llm=FakeLLMProvider([_mcq_response(), _short_answer_response()]),
    )

    async with async_session_factory() as session:
        questions = list((await session.exec(select(Question).where(Question.quiz_id == quiz_id))).all())

    by_path = {q.file_path: q.subsystem_key for q in questions}
    assert by_path == {"app/api/a.py": "app/api", "app/worker/b.py": "app/worker"}


async def test_question_from_an_unclaimed_file_has_no_subsystem_key(pending_repo_factory) -> None:
    # A file in no subsystem, or a snapshot indexed before subsystems existed.
    # Null rather than a guess — aggregation buckets these as "ungrouped".
    repo_id, snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")
    guide_id = await _make_guide_with_citations(repo_id, snapshot_id, ["stray.py", "other.py"])
    quiz_id = await _make_pending_quiz(repo_id, guide_id)

    await generate_quiz(
        {}, quiz_id=quiz_id, study_guide_id=guide_id,
        llm=FakeLLMProvider([_mcq_response(), _short_answer_response()]),
    )

    async with async_session_factory() as session:
        questions = list((await session.exec(select(Question).where(Question.quiz_id == quiz_id))).all())

    assert all(q.subsystem_key is None for q in questions)


async def test_subsystem_keys_survive_a_reindex(pending_repo_factory) -> None:
    # The whole point: a re-index replaces every Section, Citation, and Question
    # row, so mastery can only span versions if the key it aggregates on is
    # stable. Two snapshots of the same repo, quizzed separately, must produce
    # the same keys for the same directories.
    repo_id, first_snapshot_id = await pending_repo_factory(SourceType.git_url, "https://example.com/repo.git")
    first_guide = await _make_guide_with_citations(repo_id, first_snapshot_id, ["app/api/a.py", "app/worker/b.py"])
    await _add_subsystem(first_snapshot_id, "app/api", "HTTP API", ["app/api/a.py"])
    await _add_subsystem(first_snapshot_id, "app/worker", "Background worker", ["app/worker/b.py"])
    first_quiz = await _make_pending_quiz(repo_id, first_guide)
    await generate_quiz(
        {}, quiz_id=first_quiz, study_guide_id=first_guide,
        llm=FakeLLMProvider([_mcq_response(), _short_answer_response()]),
    )

    # Second snapshot for the same repo, with a differently-named subsystem for
    # the same directory — the generated *name* can drift between runs; the key
    # is what must not.
    async with async_session_factory() as session:
        second_snapshot = AnalysisSnapshot(repo_id=repo_id, status=SnapshotStatus.ready)
        session.add(second_snapshot)
        await session.commit()
        await session.refresh(second_snapshot)
    second_guide = await _make_guide_with_citations(
        repo_id, second_snapshot.id, ["app/api/a.py", "app/worker/b.py"], version=2
    )
    await _add_subsystem(second_snapshot.id, "app/api", "Web layer", ["app/api/a.py"])
    await _add_subsystem(second_snapshot.id, "app/worker", "Job runner", ["app/worker/b.py"])
    second_quiz = await _make_pending_quiz(repo_id, second_guide)
    await generate_quiz(
        {}, quiz_id=second_quiz, study_guide_id=second_guide,
        llm=FakeLLMProvider([_mcq_response(), _short_answer_response()]),
    )

    async with async_session_factory() as session:
        first_questions = list((await session.exec(select(Question).where(Question.quiz_id == first_quiz))).all())
        second_questions = list((await session.exec(select(Question).where(Question.quiz_id == second_quiz))).all())

    assert {q.file_path: q.subsystem_key for q in first_questions} == {
        q.file_path: q.subsystem_key for q in second_questions
    }

"""Quiz generation orchestration (docs/design/original-project-plan.md §12 Phase 5).

Unlike Layer B (semantics/orchestrator.py) and study-guide assembly
(generation/study_guide_builder.py), this module never touches the cloned
repo on disk. By the time a user asks for a quiz, indexing has long since
finished and worker/pipeline.py has deleted the temp workspace (ADR-008) — so
questions are built entirely from Citation rows the study guide already
persisted (file_path/line_start/line_end + a real snippet_text captured back
when the repo *was* still on disk, per generation/citation.py). This also
means quiz generation never needs a source checkout of its own, git_url vs.
zip_upload doesn't matter here, and there's no cleanup_workspace step.

Mirrors run_layer_b's session discipline: read session closed before any LLM
call, LLM calls made with no session open, then a short write session after
(NullPool — see orchestrator.py's own comment on why holding a session open
across slow external calls is a real cost here, not just a style choice).
"""

from uuid import UUID

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Citation, FillBlankMode, Question, QuestionType, Quiz, QuizStatus, Section, SectionType
from app.db.session import async_session_factory
from app.quizzing.fill_blank_generator import FillBlankResult, generate_fill_blank_questions
from app.quizzing.mcq_generator import MCQResult, generate_mcq_questions
from app.quizzing.seeds import QuestionSeed
from app.semantics.llm_provider import LLMProvider

# JUDGMENT CALL — the original plan doesn't say how many questions a quiz
# should have or which citations to draw them from. Capped for the same
# reason identify_decision_points (tradeoff_extractor.py) caps candidates:
# bounds billed LLM calls per generate request, and a 40-question quiz isn't
# a more useful study tool than a focused one. Retune against real repos.
MAX_QUESTIONS_PER_QUIZ = 12

# Deep-dive/trade-off/architecture citations ground a specific, substantive
# claim about the code; glossary citations just mark where a term was first
# defined, and overview citations are one-line entry-point blurbs — thinner
# material to build a real question from. Lower sorts first.
_SECTION_PRIORITY = {
    SectionType.deep_dive: 0,
    SectionType.tradeoffs: 0,
    SectionType.architecture: 1,
    SectionType.glossary: 2,
    SectionType.overview: 3,
}


def identify_question_seeds(
    citations: list[Citation],
    section_type_by_id: dict[UUID, SectionType],
    *,
    limit: int = MAX_QUESTIONS_PER_QUIZ,
) -> list[QuestionSeed]:
    # Many citations share the same (file_path, line_start, line_end) — a
    # glossary entry and a deep-dive paragraph both cite the same module
    # range, the same dedup case study_guide_builder.py's snippet_cache
    # exists for. Keep the highest-priority section's version of each range;
    # a second, lower-priority claim about the exact same lines wouldn't
    # make a meaningfully different question anyway.
    best_by_range: dict[tuple[str, int, int], Citation] = {}
    best_priority: dict[tuple[str, int, int], int] = {}
    for citation in citations:
        key = (citation.file_path, citation.line_start, citation.line_end)
        priority = _SECTION_PRIORITY.get(section_type_by_id.get(citation.section_id), 3)
        if key not in best_priority or priority < best_priority[key]:
            best_priority[key] = priority
            best_by_range[key] = citation

    ranked = sorted(
        best_by_range.values(),
        key=lambda c: (best_priority[(c.file_path, c.line_start, c.line_end)], c.file_path, c.line_start),
    )
    return [
        QuestionSeed(
            claim_excerpt=c.claim_excerpt,
            snippet_text=c.snippet_text,
            file_path=c.file_path,
            line_start=c.line_start,
            line_end=c.line_end,
        )
        for c in ranked[:limit]
    ]


async def create_pending_quiz(session: AsyncSession, repo_id: UUID, study_guide_id: UUID) -> Quiz:
    """The API creates this synchronously, before enqueueing the generation
    job, so POST /quizzes/{repo_id}/generate has an id to return immediately —
    same pattern as create_pending_snapshot (analysis/snapshot.py)."""
    quiz = Quiz(repo_id=repo_id, study_guide_id=study_guide_id, status=QuizStatus.generating)
    session.add(quiz)
    await session.commit()
    await session.refresh(quiz)
    return quiz


async def fail_quiz(session: AsyncSession, quiz_id: UUID) -> None:
    quiz = await session.get(Quiz, quiz_id)
    if quiz is None:
        return  # repo/quiz deleted out from under an in-flight job — nothing to update
    quiz.status = QuizStatus.failed
    session.add(quiz)
    await session.commit()


async def run_quiz_generation(llm: LLMProvider, quiz_id: UUID, study_guide_id: UUID) -> None:
    async with async_session_factory() as session:
        sections = list(
            (await session.exec(select(Section).where(Section.study_guide_id == study_guide_id))).all()
        )
        section_type_by_id = {s.id: s.section_type for s in sections}
        section_ids = list(section_type_by_id)
        citations: list[Citation] = []
        if section_ids:
            citations = list(
                (await session.exec(select(Citation).where(Citation.section_id.in_(section_ids)))).all()
            )

    seeds = identify_question_seeds(citations, section_type_by_id)
    # Alternate by seed position so both question types draw from across the
    # whole ranked list rather than mcq claiming every high-priority seed —
    # simpler than round-robin-ing generator calls, and generator functions
    # already take a list per Phase 2's summarize_modules/extract_tradeoffs
    # shape (one LLM call per item, not one call for the whole batch).
    mcq_seeds = seeds[0::2]
    fill_blank_seeds = seeds[1::2]

    mcq_results = await generate_mcq_questions(llm, mcq_seeds)
    fill_blank_results = await generate_fill_blank_questions(llm, fill_blank_seeds)

    # Re-sorted back into the seeds' original (file_path, line_start) order —
    # generating the two types via separate calls above means their results
    # come back in two separate lists, not the interleaved order a reader of
    # the source study guide would encounter them in.
    combined: list[MCQResult | FillBlankResult] = [*mcq_results, *fill_blank_results]
    combined.sort(key=lambda r: (r.seed.file_path, r.seed.line_start))

    async with async_session_factory() as session:
        # arq is at-least-once — a redelivered generate job must not
        # duplicate every question on top of a prior attempt's rows. Same
        # delete-then-insert idempotency as run_layer_b/persist_study_guide.
        await session.exec(delete(Question).where(Question.quiz_id == quiz_id))

        for order, result in enumerate(combined):
            if isinstance(result, MCQResult):
                question = Question(
                    quiz_id=quiz_id,
                    question_type=QuestionType.mcq,
                    order=order,
                    prompt=result.prompt,
                    choices=result.choices,
                    correct_index=result.correct_index,
                    explanation=result.explanation,
                    file_path=result.seed.file_path,
                    line_start=result.seed.line_start,
                    line_end=result.seed.line_end,
                    prompt_version=result.prompt_version,
                    model=result.model,
                )
            else:
                question = Question(
                    quiz_id=quiz_id,
                    question_type=QuestionType.fill_blank,
                    order=order,
                    prompt=result.blanked_text,
                    fill_blank_mode=FillBlankMode(result.mode),
                    correct_answer=result.correct_answer,
                    acceptable_alternatives=result.acceptable_alternatives,
                    file_path=result.seed.file_path,
                    line_start=result.seed.line_start,
                    line_end=result.seed.line_end,
                    prompt_version=result.prompt_version,
                    model=result.model,
                )
            session.add(question)

        # Final status commits with the data it describes, not a later
        # separate one — same invariant run_layer_b/persist_study_guide
        # follow, for the same crash-window reason.
        quiz = await session.get(Quiz, quiz_id)
        if quiz is not None:
            quiz.status = QuizStatus.ready
            session.add(quiz)

        await session.commit()

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

from sqlalchemy.exc import IntegrityError
from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import (
    Citation,
    FeedbackMode,
    Question,
    QuestionType,
    Quiz,
    QuizStatus,
    Section,
    SectionType,
    StudyGuide,
    Subsystem,
)
from app.db.session import async_session_factory
from app.quizzing.mcq_generator import MCQResult, generate_mcq_questions
from app.quizzing.seeds import QuestionSeed
from app.quizzing.short_answer_generator import (
    ShortAnswerResult,
    generate_short_answer_questions,
)
from app.semantics.llm_provider import LLMProvider

# JUDGMENT CALL — the original plan doesn't say how many questions a quiz
# should have or which citations to draw them from. Capped for the same
# reason identify_decision_points (tradeoff_extractor.py) caps candidates:
# bounds billed LLM calls per generate request, and a 40-question quiz isn't
# a more useful study tool than a focused one. Retune against real repos.
MAX_QUESTIONS_PER_QUIZ = 12

# Trade-off and architecture citations ground the "why" material — a decision
# and its reasoning, or one of the architecture narrative's why-sections (#52).
# Deep dives describe what one file does. Glossary citations just mark where a
# term was first defined, and overview citations are one-line entry-point
# blurbs. Lower sorts first.
#
# Architecture moved up from its own tier to join trade-offs when the narrative
# landed: before that, an architecture citation grounded a bullet in an evidence
# list, which really was thinner material than a deep dive.
_SECTION_PRIORITY = {
    SectionType.tradeoffs: 0,
    SectionType.architecture: 0,
    SectionType.deep_dive: 1,
    SectionType.glossary: 2,
    SectionType.overview: 3,
}


def _normalized_claim(claim_excerpt: str) -> str:
    return " ".join(claim_excerpt.split()).casefold()


def _round_robin(buckets: dict[str, list[Citation]]) -> list[Citation]:
    """One citation from each bucket in turn, buckets in key order. Taking a
    flat prefix of a sorted list instead is what let a whole quiz come from
    whichever directory sorts first (#51)."""
    ordered: list[Citation] = []
    keys = sorted(buckets)
    index = 0
    while True:
        added = False
        for key in keys:
            bucket = buckets[key]
            if index < len(bucket):
                ordered.append(bucket[index])
                added = True
        if not added:
            return ordered
        index += 1


def identify_question_seeds(
    citations: list[Citation],
    section_type_by_id: dict[UUID, SectionType],
    *,
    subsystem_key_by_file: dict[str, str] | None = None,
    limit: int = MAX_QUESTIONS_PER_QUIZ,
) -> list[QuestionSeed]:
    subsystem_key_by_file = subsystem_key_by_file or {}

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

    # Then dedup by the claim itself, which the range check above can't catch
    # (#51). study_guide_builder gives every evidence_ref of a trade-off card
    # the same claim_excerpt — the whole card text — and every supporting path
    # of an architecture why-section likewise. Those are different line ranges
    # carrying identical claims, so all of them used to survive and generate
    # several questions from one piece of material, phrased differently. That
    # is exactly the reported symptom.
    best_by_claim: dict[str, Citation] = {}
    for citation in best_by_range.values():
        claim = _normalized_claim(citation.claim_excerpt)
        priority = best_priority[(citation.file_path, citation.line_start, citation.line_end)]
        current = best_by_claim.get(claim)
        if current is None:
            best_by_claim[claim] = citation
            continue
        current_priority = best_priority[(current.file_path, current.line_start, current.line_end)]
        if (priority, citation.file_path, citation.line_start) < (
            current_priority,
            current.file_path,
            current.line_start,
        ):
            best_by_claim[claim] = citation

    # Spread within each priority tier across subsystems, then concatenate the
    # tiers in priority order — so the best material still comes first, but a
    # quiz drawn from it reaches more than one part of the system. Files with
    # no subsystem (a snapshot indexed before subsystems existed) share one
    # bucket rather than being dropped.
    by_tier: dict[int, dict[str, list[Citation]]] = {}
    for citation in best_by_claim.values():
        priority = best_priority[(citation.file_path, citation.line_start, citation.line_end)]
        bucket_key = subsystem_key_by_file.get(citation.file_path, "")
        by_tier.setdefault(priority, {}).setdefault(bucket_key, []).append(citation)

    ranked: list[Citation] = []
    for priority in sorted(by_tier):
        buckets = by_tier[priority]
        for bucket in buckets.values():
            bucket.sort(key=lambda c: (c.file_path, c.line_start))
        ranked.extend(_round_robin(buckets))

    return [
        QuestionSeed(
            citation_id=c.id,
            claim_excerpt=c.claim_excerpt,
            snippet_text=c.snippet_text,
            file_path=c.file_path,
            line_start=c.line_start,
            line_end=c.line_end,
            subsystem_key=subsystem_key_by_file.get(c.file_path),
        )
        for c in ranked[:limit]
    ]


async def _find_generating_quiz(session: AsyncSession, study_guide_id: UUID) -> Quiz | None:
    return (
        await session.exec(
            select(Quiz).where(Quiz.study_guide_id == study_guide_id, Quiz.status == QuizStatus.generating)
        )
    ).first()


async def create_pending_quiz(
    session: AsyncSession, repo_id: UUID, study_guide_id: UUID, feedback_mode: FeedbackMode = FeedbackMode.end_of_quiz
) -> tuple[Quiz, bool]:
    """The API creates this synchronously, before enqueueing the generation
    job, so POST /quizzes/{repo_id}/generate has an id to return immediately —
    same pattern as create_pending_snapshot (analysis/snapshot.py).

    Returns (quiz, created) — `created` is False when an already-`generating`
    quiz for this study guide is reused instead of starting a second one
    (`feedback_mode` is then whatever the reused quiz was already created
    with, not this call's argument — it's the same generation job, not a new
    one). The initial check below isn't itself race-free (two concurrent
    calls can both pass it before either commits — a double-click, a retry,
    two tabs), so Quiz's partial unique index (db/models.py) is the real
    guarantee: a losing insert's IntegrityError is caught and turned into
    "reuse the winner's row" rather than a 500 (found via the Phase 5 Codex
    review, second pass)."""
    existing = await _find_generating_quiz(session, study_guide_id)
    if existing is not None:
        return existing, False

    quiz = Quiz(repo_id=repo_id, study_guide_id=study_guide_id, status=QuizStatus.generating, feedback_mode=feedback_mode)
    session.add(quiz)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_generating_quiz(session, study_guide_id)
        if existing is None:
            raise  # a real, different failure — don't mask it as "someone else won"
        return existing, False
    await session.refresh(quiz)
    return quiz, True


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

        # Subsystems hang off the snapshot, not the study guide, so this needs
        # the guide's own row to get there. Used only to spread seeds across
        # the codebase (#51) — a missing or empty set degrades to the previous
        # single-bucket behavior rather than failing.
        guide = await session.get(StudyGuide, study_guide_id)
        subsystem_key_by_file: dict[str, str] = {}
        if guide is not None:
            subsystems = list(
                (await session.exec(select(Subsystem).where(Subsystem.snapshot_id == guide.snapshot_id))).all()
            )
            for subsystem in subsystems:
                for file_path in subsystem.file_paths:
                    subsystem_key_by_file[file_path] = subsystem.key

    seeds = identify_question_seeds(citations, section_type_by_id, subsystem_key_by_file=subsystem_key_by_file)
    # Alternate by seed position so both question types draw from across the
    # whole ranked list rather than mcq claiming every high-priority seed —
    # simpler than round-robin-ing generator calls, and generator functions
    # already take a list per Phase 2's summarize_modules/extract_tradeoffs
    # shape (one LLM call per item, not one call for the whole batch).
    #
    # short_answer replaced fill_blank here: a blanked term, however well
    # chosen, still tests "guess the word I'm thinking of" more than
    # understanding. The open how/why question graded against a rubric is
    # the quiz counterpart to Phase 6's architecture narrative. fill_blank
    # stays gradable for quizzes generated before this; nothing new is made.
    mcq_seeds = seeds[0::2]
    short_answer_seeds = seeds[1::2]

    mcq_results = await generate_mcq_questions(llm, mcq_seeds)
    short_answer_results = await generate_short_answer_questions(llm, short_answer_seeds)

    # Re-sorted back into the seeds' original (file_path, line_start) order —
    # generating the two types via separate calls above means their results
    # come back in two separate lists, not the interleaved order a reader of
    # the source study guide would encounter them in.
    combined: list[MCQResult | ShortAnswerResult] = [*mcq_results, *short_answer_results]
    combined.sort(key=lambda r: (r.seed.file_path, r.seed.line_start))

    if not combined:
        # No usable citations (a guide too thin to seed any questions from),
        # or every generated result failed its own generator's validation
        # (mcq_generator.py's correct_index check, short_answer_generator.py's
        # rubric check). Marking `ready` with zero questions would let
        # the client poll into a "successful" quiz QuizTaker can't render —
        # it indexes straight into questions[0] (found via the Phase 5 Codex
        # review). Raising here routes through quiz_pipeline.py's normal
        # except-Exception handler, which marks the quiz `failed` instead.
        raise RuntimeError("quiz generation produced no usable questions")

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
                    subsystem_key=result.seed.subsystem_key,
                    source_citation_id=result.seed.citation_id,
                    prompt_version=result.prompt_version,
                    model=result.model,
                )
            else:
                question = Question(
                    quiz_id=quiz_id,
                    question_type=QuestionType.short_answer,
                    order=order,
                    prompt=result.prompt,
                    # The model answer rides in correct_answer (shown after
                    # grading, never matched against); the rubric is what
                    # the grader actually judges.
                    correct_answer=result.model_answer,
                    rubric=result.rubric,
                    file_path=result.seed.file_path,
                    line_start=result.seed.line_start,
                    line_end=result.seed.line_end,
                    subsystem_key=result.seed.subsystem_key,
                    source_citation_id=result.seed.citation_id,
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

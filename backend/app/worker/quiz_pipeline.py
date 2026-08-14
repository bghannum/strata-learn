"""Quiz generation as an arq job (docs/design/original-project-plan.md §12 Phase 5),
mirroring index_repo's (worker/pipeline.py) job-per-request shape and terminal-state
guarantees, but much simpler: no source acquisition, no cleanup_workspace, no
multi-stage status transitions — quiz generation reads already-persisted
Section/Citation rows (see quizzing/generation.py's module docstring for why)
and does its LLM work in one pass.

No WS progress channel here, unlike index_repo — the plan's own Phase 5
endpoint list (§12) has no progress endpoint, and generation.py's
MAX_QUESTIONS_PER_QUIZ bound keeps this to a handful of cheap-tier LLM calls,
short enough that GET /quizzes/{id} polling (see api/quizzes.py) is enough.
Revisit if a real checkpoint run shows this taking long enough to need one.
"""

import asyncio
from uuid import UUID

from app.db.models import Quiz, QuizStatus
from app.db.session import async_session_factory
from app.quizzing.generation import fail_quiz, run_quiz_generation
from app.semantics.llm_provider import AnthropicProvider, LLMProvider
from app.config import settings


async def generate_quiz(
    ctx: dict, quiz_id: UUID, study_guide_id: UUID, llm: LLMProvider | None = None
) -> None:
    try:
        # Short-circuit a redelivery of an already-finished job (arq is
        # at-least-once) — same reasoning as index_repo's "already ready"
        # guard: without this, a redelivery would re-run every billed LLM
        # call for a quiz that's already sitting there `ready`.
        async with async_session_factory() as session:
            existing = await session.get(Quiz, quiz_id)
        if existing is not None and existing.status in (QuizStatus.ready, QuizStatus.failed):
            return

        llm = llm or AnthropicProvider(api_key=settings.anthropic_api_key)  # type: ignore[arg-type]
        await run_quiz_generation(llm, quiz_id, study_guide_id)
    except asyncio.CancelledError:
        # A job_timeout expiry or worker shutdown — same asymmetry index_repo's
        # own handler documents (TimeoutError vs. CancelledError, confirmed
        # empirically there): always mark failed rather than guess whether arq
        # will retry, so the quiz never sits at `generating` forever with
        # GET /quizzes/{id} looking permanently stuck to the client.
        async with async_session_factory() as session:
            await fail_quiz(session, quiz_id)
        raise
    except Exception:
        async with async_session_factory() as session:
            await fail_quiz(session, quiz_id)
        raise

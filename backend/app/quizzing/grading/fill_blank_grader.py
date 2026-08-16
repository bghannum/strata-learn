"""§10.2 — fill-in-the-blank grading. Exact/alternative match first, always.
Only a concept-mode miss falls through to the LLM-judge prompt
(docs/prompts/fill_blank_concept_grader.v1.md); code-mode answers are exact
match only, per §10.2's own split — a code token either matches the real
implementation or it doesn't, there's no "conceptually equivalent" variable
name.
"""

from typing import Literal

from pydantic import BaseModel

from app.db.models import FillBlankMode, Question
from app.semantics.llm_provider import LLMProvider, Message, require_parsed
from app.semantics.prompts import load_prompt


class FillBlankGradeOutput(BaseModel):
    # Literal, not a bare float — §10.2's rubric only defines 0.0/0.5/1.0.
    # This constrains the model's structured-output schema itself (Anthropic
    # enforces it at generation time, not just after the fact), so an
    # in-range-but-off-rubric value like 0.7 can't reach Attempt.score's
    # average in the first place (found via the Phase 5 Codex review — a
    # plain float with a post-hoc [0,1] clamp let 0.7 straight through).
    score: Literal[0.0, 0.5, 1.0]
    feedback: str


class FillBlankLLMUnavailableError(RuntimeError):
    """Raised only when a concept-mode miss needs an unavailable LLM."""


def _normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def _matches_answer_key(question: Question, answer_text: str) -> bool:
    candidates = {_normalize(question.correct_answer or "")}
    candidates.update(_normalize(alt) for alt in question.acceptable_alternatives)
    return _normalize(answer_text) in candidates


async def grade_fill_blank(llm: LLMProvider | None, question: Question, answer_text: str) -> tuple[float, str]:
    if _matches_answer_key(question, answer_text):
        return 1.0, "Correct."

    if question.fill_blank_mode == FillBlankMode.code:
        return 0.0, f'Incorrect. The expected answer was "{question.correct_answer}".'

    if llm is None:
        raise FillBlankLLMUnavailableError("concept-mode grading requires a configured LLM provider")

    template = load_prompt("fill_blank_concept_grader")
    input_text = template.render_input(
        correct_answer=question.correct_answer,
        alternatives=", ".join(question.acceptable_alternatives) or "none",
        student_answer=answer_text,
    )
    response = await llm.complete(
        system=template.system,
        messages=[Message(role="user", content=input_text)],
        response_schema=FillBlankGradeOutput,
    )
    # Deliberately *not* caught here, unlike the indexing-pipeline call sites:
    # there is no honest fallback for a grade. Defaulting to 0.0 would record
    # a wrong score against the student for what is an infrastructure failure,
    # and 1.0 would do the reverse. LLMOutputError propagates to the endpoint,
    # which returns 503 so nothing is persisted and the answer can be
    # resubmitted (same disposition as FillBlankLLMUnavailableError).
    output = require_parsed(response, FillBlankGradeOutput)
    return output.score, output.feedback

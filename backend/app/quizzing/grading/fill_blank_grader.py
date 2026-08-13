"""§10.2 — fill-in-the-blank grading. Exact/alternative match first, always.
Only a concept-mode miss falls through to the LLM-judge prompt
(docs/prompts/fill_blank_concept_grader.v1.md); code-mode answers are exact
match only, per §10.2's own split — a code token either matches the real
implementation or it doesn't, there's no "conceptually equivalent" variable
name.
"""

from pydantic import BaseModel

from app.db.models import FillBlankMode, Question
from app.semantics.llm_provider import LLMProvider, Message
from app.semantics.prompts import load_prompt


class FillBlankGradeOutput(BaseModel):
    score: float
    feedback: str


def _normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def _matches_answer_key(question: Question, answer_text: str) -> bool:
    candidates = {_normalize(question.correct_answer or "")}
    candidates.update(_normalize(alt) for alt in question.acceptable_alternatives)
    return _normalize(answer_text) in candidates


async def grade_fill_blank(llm: LLMProvider, question: Question, answer_text: str) -> tuple[float, str]:
    if _matches_answer_key(question, answer_text):
        return 1.0, "Correct."

    if question.fill_blank_mode == FillBlankMode.code:
        return 0.0, f'Incorrect. The expected answer was "{question.correct_answer}".'

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
    output = response.parsed
    assert isinstance(output, FillBlankGradeOutput)

    # Clamp — the prompt asks for exactly 0.0/0.5/1.0, but nothing stops a
    # model from returning e.g. 0.7; a score outside the documented range
    # would silently corrupt Attempt.score's average (generation.py never
    # validates LLM-judge output the way mcq_generator/fill_blank_generator
    # validate their own, since there's no "drop the result" option once a
    # student is waiting on feedback for the answer they just submitted).
    score = max(0.0, min(1.0, output.score))
    return score, output.feedback

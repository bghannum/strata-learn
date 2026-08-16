"""Short-answer grading: always the LLM judge, against the question's rubric.

No exact-match fast path — a paragraph has none. The judge is asked which
rubric points the answer addressed (one boolean each) plus teaching feedback;
the score is computed *here* as hits / total. That keeps the number
deterministic given the judgment, keeps it on Attempt.score's [0, 1] scale
without a rubric-literal type, and gives the results page coverage to show
rather than just a score.
"""

from pydantic import BaseModel

from app.db.models import Question
from app.semantics.llm_provider import LLMProvider, Message, require_parsed
from app.semantics.prompts import load_prompt


class ShortAnswerGradeOutput(BaseModel):
    hits: list[bool]
    feedback: str


class ShortAnswerLLMUnavailableError(RuntimeError):
    """Raised when a short-answer submission needs an unavailable LLM."""


class ShortAnswerRubricMismatchError(RuntimeError):
    """The judge returned a hits list that doesn't line up with the rubric.
    The endpoint maps this to the same 503-and-resubmit as LLMOutputError —
    a misaligned list would silently grade the wrong points, which is worse
    than asking the learner to send the answer again."""

    def __init__(self, expected: int, got: int) -> None:
        super().__init__(f"rubric has {expected} points but the judge returned {got} verdicts")
        self.expected = expected
        self.got = got


def _render_rubric(rubric: list[str]) -> str:
    return "\n".join(f"{i}. {point}" for i, point in enumerate(rubric, start=1))


async def grade_short_answer(
    llm: LLMProvider | None, question: Question, answer_text: str
) -> tuple[float, str, list[bool]]:
    """Returns (score, feedback, hits). `hits` is persisted on the submission
    so results can show which points landed."""
    rubric = [str(p) for p in (question.rubric or []) if str(p).strip()]
    if not rubric:
        # A question persisted without a rubric can't be graded honestly;
        # the generator refuses to produce one, so this is a data bug.
        raise ValueError(f"short_answer question {question.id} has no rubric")
    if llm is None:
        raise ShortAnswerLLMUnavailableError("short-answer grading requires a configured LLM provider")

    template = load_prompt("short_answer_grader")
    input_text = template.render_input(
        question=question.prompt,
        model_answer=question.correct_answer or "",
        rubric=_render_rubric(rubric),
        student_answer=answer_text,
    )
    response = await llm.complete(
        system=template.system,
        messages=[Message(role="user", content=input_text)],
        response_schema=ShortAnswerGradeOutput,
    )
    # Deliberately not caught here — same reasoning as fill_blank_grader.py:
    # there's no honest fallback for a grade, so the endpoint returns 503 and
    # nothing is persisted.
    output = require_parsed(response, ShortAnswerGradeOutput)
    if len(output.hits) != len(rubric):
        raise ShortAnswerRubricMismatchError(len(rubric), len(output.hits))

    hits = [bool(h) for h in output.hits]
    score = sum(hits) / len(hits)
    return score, output.feedback.strip(), hits

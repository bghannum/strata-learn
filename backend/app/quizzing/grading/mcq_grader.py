"""§10.1 — MCQ grading is deterministic: no LLM call, no ambiguity."""

from app.db.models import Question


def grade_mcq(question: Question, selected_index: int) -> tuple[float, str]:
    is_correct = selected_index == question.correct_index
    score = 1.0 if is_correct else 0.0
    # explanation covers why the correct choice is right *and* why the
    # others aren't (mcq_generator.v1.md's prompt asks for both) — shown
    # regardless of outcome, not just on a miss.
    return score, question.explanation or ""

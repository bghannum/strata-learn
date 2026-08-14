import uuid

from app.db.models import Question, QuestionType
from app.quizzing.grading.mcq_grader import grade_mcq


def _question(correct_index: int) -> Question:
    return Question(
        quiz_id=uuid.uuid4(), question_type=QuestionType.mcq, order=0, prompt="q",
        choices=["a", "b", "c"], correct_index=correct_index, explanation="because reasons",
        file_path="app.py", line_start=1, line_end=1, prompt_version="v1", model="fake-model",
    )


def test_grade_mcq_correct_selection() -> None:
    score, feedback = grade_mcq(_question(correct_index=1), selected_index=1)
    assert score == 1.0
    assert feedback == "because reasons"


def test_grade_mcq_incorrect_selection_still_returns_explanation() -> None:
    score, feedback = grade_mcq(_question(correct_index=1), selected_index=0)
    assert score == 0.0
    assert feedback == "because reasons"  # explanation shown regardless of outcome

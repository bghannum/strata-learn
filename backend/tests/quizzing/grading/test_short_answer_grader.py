import uuid

import pytest

from app.db.models import Question, QuestionType
from app.quizzing.grading.short_answer_grader import (
    ShortAnswerGradeOutput,
    ShortAnswerLLMUnavailableError,
    ShortAnswerRubricMismatchError,
    grade_short_answer,
)
from app.semantics.llm_provider import FakeLLMProvider, LLMOutputError, LLMResponse


def _question(rubric: list[str] | None) -> Question:
    return Question(
        quiz_id=uuid.uuid4(), question_type=QuestionType.short_answer, order=0,
        prompt="Why does indexing run in a background job?",
        correct_answer="Because cloning and parsing take minutes and a request would time out; status is persisted.",
        rubric=rubric, file_path="a.py", line_start=1, line_end=2, prompt_version="v1", model="fake",
    )


def _judge(hits: list[bool], feedback: str = "fb") -> FakeLLMProvider:
    return FakeLLMProvider(
        [LLMResponse(text="", parsed=ShortAnswerGradeOutput(hits=hits, feedback=feedback), model="fake", stop_reason="end_turn", usage={})]
    )


async def test_score_is_the_fraction_of_rubric_points_hit() -> None:
    llm = _judge([True, False, True], "You got the timing and the persisted status, but not the timeout.")
    score, feedback, hits = await grade_short_answer(llm, _question(["slow", "timeout", "persisted status"]), "answer")
    assert score == pytest.approx(2 / 3)
    assert hits == [True, False, True]
    assert feedback.startswith("You got")


async def test_all_and_none() -> None:
    assert (await grade_short_answer(_judge([True, True]), _question(["a", "b"]), "x"))[0] == 1.0
    assert (await grade_short_answer(_judge([False, False]), _question(["a", "b"]), "x"))[0] == 0.0


async def test_the_prompt_carries_question_model_answer_numbered_rubric_and_answer() -> None:
    llm = _judge([True, True])
    await grade_short_answer(llm, _question(["first point", "second point"]), "the learner's words")
    sent = llm.calls[0].messages[0].content
    assert "Why does indexing" in sent
    assert "Because cloning" in sent
    # Numbered, one per line, so `hits` has an unambiguous order.
    assert "1. first point\n2. second point" in sent
    assert "the learner's words" in sent


async def test_a_misaligned_verdict_list_is_refused_not_guessed_at() -> None:
    with pytest.raises(ShortAnswerRubricMismatchError):
        await grade_short_answer(_judge([True]), _question(["a", "b", "c"]), "x")


async def test_no_llm_raises_the_unavailable_error() -> None:
    with pytest.raises(ShortAnswerLLMUnavailableError):
        await grade_short_answer(None, _question(["a", "b"]), "x")


async def test_a_question_without_a_rubric_is_a_data_bug() -> None:
    with pytest.raises(ValueError):
        await grade_short_answer(_judge([True]), _question(None), "x")


async def test_an_unparsable_judge_response_propagates() -> None:
    llm = FakeLLMProvider([LLMResponse(text="", parsed=None, model="fake", stop_reason="max_tokens", usage={})])
    with pytest.raises(LLMOutputError):
        await grade_short_answer(llm, _question(["a", "b"]), "x")

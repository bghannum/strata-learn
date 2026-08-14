import uuid

import pytest
from pydantic import ValidationError

from app.db.models import FillBlankMode, Question, QuestionType
from app.quizzing.grading.fill_blank_grader import (
    FillBlankGradeOutput,
    FillBlankLLMUnavailableError,
    grade_fill_blank,
)
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse


def _question(mode: FillBlankMode, correct_answer: str = "arq", alternatives: list[str] | None = None) -> Question:
    return Question(
        quiz_id=uuid.uuid4(), question_type=QuestionType.fill_blank, order=0, prompt="uses ___",
        fill_blank_mode=mode, correct_answer=correct_answer, acceptable_alternatives=alternatives or [],
        file_path="app.py", line_start=1, line_end=1, prompt_version="v1", model="fake-model",
    )


async def test_grade_fill_blank_exact_match_skips_llm() -> None:
    llm = FakeLLMProvider([])  # would raise if called
    score, feedback = await grade_fill_blank(llm, _question(FillBlankMode.code), "arq")
    assert score == 1.0
    assert feedback == "Correct."
    assert llm.calls == []


async def test_grade_fill_blank_case_and_whitespace_insensitive_match() -> None:
    llm = FakeLLMProvider([])
    score, _ = await grade_fill_blank(llm, _question(FillBlankMode.code), "  ARQ  ")
    assert score == 1.0


async def test_grade_fill_blank_matches_acceptable_alternative() -> None:
    llm = FakeLLMProvider([])
    question = _question(FillBlankMode.concept, correct_answer="arq", alternatives=["arq queue", "the arq library"])
    score, _ = await grade_fill_blank(llm, question, "the arq library")
    assert score == 1.0


async def test_grade_fill_blank_code_mode_miss_never_calls_llm() -> None:
    # §10.2: code-mode answers are exact match only — no LLM-judge fallback.
    llm = FakeLLMProvider([])
    score, feedback = await grade_fill_blank(llm, _question(FillBlankMode.code), "celery")
    assert score == 0.0
    assert "arq" in feedback
    assert llm.calls == []


async def test_grade_fill_blank_deterministic_paths_accept_no_provider() -> None:
    exact_score, _ = await grade_fill_blank(None, _question(FillBlankMode.concept), "arq")
    code_miss_score, _ = await grade_fill_blank(None, _question(FillBlankMode.code), "celery")

    assert exact_score == 1.0
    assert code_miss_score == 0.0


async def test_grade_fill_blank_concept_miss_requires_provider() -> None:
    with pytest.raises(FillBlankLLMUnavailableError):
        await grade_fill_blank(None, _question(FillBlankMode.concept), "some background job thing")


async def test_grade_fill_blank_concept_mode_miss_falls_back_to_llm_judge() -> None:
    llm = FakeLLMProvider(
        [LLMResponse(text="", parsed=FillBlankGradeOutput(score=0.5, feedback="close but imprecise"), model="fake-model", stop_reason="end_turn", usage={})]
    )
    question = _question(FillBlankMode.concept, correct_answer="arq", alternatives=["arq queue"])

    score, feedback = await grade_fill_blank(llm, question, "some background job thing")

    assert score == 0.5
    assert feedback == "close but imprecise"
    assert len(llm.calls) == 1
    call_input = llm.calls[0].messages[0].content
    assert "arq" in call_input
    assert "some background job thing" in call_input


def test_fill_blank_grade_output_rejects_off_rubric_score() -> None:
    # §10.2's rubric only defines 0.0/0.5/1.0 — Literal makes this a schema
    # constraint the LLM provider enforces at generation time, not just a
    # post-hoc clamp (found via the Phase 5 Codex review: a plain float with
    # a [0,1] clamp let an in-range-but-off-rubric value like 0.7 through
    # unchanged).
    with pytest.raises(ValidationError):
        FillBlankGradeOutput(score=0.7, feedback="approximately right")

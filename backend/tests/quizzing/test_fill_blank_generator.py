import uuid

from app.quizzing.fill_blank_generator import FillBlankOutput, generate_fill_blank_questions
from app.quizzing.seeds import QuestionSeed
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse


def _seed() -> QuestionSeed:
    return QuestionSeed(
        citation_id=uuid.uuid4(),
        claim_excerpt="uses arq for background jobs", snippet_text="import arq",
        file_path="app/worker.py", line_start=1, line_end=5,
    )


def _response(output: FillBlankOutput) -> LLMResponse:
    return LLMResponse(text="", parsed=output, model="fake-model", stop_reason="end_turn", usage={})


async def test_generate_fill_blank_questions_builds_result_from_parsed_output() -> None:
    llm = FakeLLMProvider(
        [
            _response(
                FillBlankOutput(
                    mode="concept",
                    blanked_text="This module uses ___ for background jobs.",
                    correct_answer="arq",
                    acceptable_alternatives=["arq queue"],
                )
            )
        ]
    )

    results = await generate_fill_blank_questions(llm, [_seed()])

    assert len(results) == 1
    result = results[0]
    assert result.mode == "concept"
    assert result.correct_answer == "arq"
    assert result.seed.file_path == "app/worker.py"


async def test_generate_fill_blank_questions_drops_missing_blank_marker() -> None:
    llm = FakeLLMProvider(
        [_response(FillBlankOutput(mode="code", blanked_text="no marker here", correct_answer="x", acceptable_alternatives=[]))]
    )

    results = await generate_fill_blank_questions(llm, [_seed()])

    assert results == []


async def test_generate_fill_blank_questions_drops_empty_correct_answer() -> None:
    llm = FakeLLMProvider(
        [_response(FillBlankOutput(mode="code", blanked_text="uses ___", correct_answer="   ", acceptable_alternatives=[]))]
    )

    results = await generate_fill_blank_questions(llm, [_seed()])

    assert results == []

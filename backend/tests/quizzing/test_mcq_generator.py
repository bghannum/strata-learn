from app.quizzing.mcq_generator import MCQOutput, generate_mcq_questions
from app.quizzing.seeds import QuestionSeed
from app.semantics.llm_provider import FakeLLMProvider, LLMResponse


def _seed() -> QuestionSeed:
    return QuestionSeed(
        claim_excerpt="uses arq for background jobs", snippet_text="import arq",
        file_path="app/worker.py", line_start=1, line_end=5,
    )


def _response(output: MCQOutput) -> LLMResponse:
    return LLMResponse(text="", parsed=output, model="fake-model", stop_reason="end_turn", usage={})


async def test_generate_mcq_questions_builds_result_from_parsed_output() -> None:
    llm = FakeLLMProvider(
        [
            _response(
                MCQOutput(
                    prompt="What does this module use for background jobs?",
                    choices=["arq", "celery", "rq", "dramatiq"],
                    correct_index=0,
                    explanation="arq is imported directly.",
                )
            )
        ]
    )

    results = await generate_mcq_questions(llm, [_seed()])

    assert len(results) == 1
    result = results[0]
    assert result.prompt == "What does this module use for background jobs?"
    assert result.correct_index == 0
    assert result.seed.file_path == "app/worker.py"
    assert result.prompt_version == "v1"
    assert result.model == "fake-model"

    # source_fact/code_snippet from the seed reached the prompt
    call = llm.calls[0]
    assert "uses arq for background jobs" in call.messages[0].content
    assert "import arq" in call.messages[0].content


async def test_generate_mcq_questions_drops_out_of_range_correct_index() -> None:
    llm = FakeLLMProvider(
        [_response(MCQOutput(prompt="q", choices=["a", "b"], correct_index=5, explanation="e"))]
    )

    results = await generate_mcq_questions(llm, [_seed()])

    assert results == []


async def test_generate_mcq_questions_drops_single_choice_question() -> None:
    llm = FakeLLMProvider(
        [_response(MCQOutput(prompt="q", choices=["only one"], correct_index=0, explanation="e"))]
    )

    results = await generate_mcq_questions(llm, [_seed()])

    assert results == []
